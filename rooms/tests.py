from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from datetime import timedelta
from urllib.parse import urlencode
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from bookings.models import Booking
from rooms.models import Room


@skipUnlessDBFeature("supports_table_check_constraints")
class RoomConstraintTests(TransactionTestCase):
    reset_sequences = True

    def test_beds_must_be_between_one_and_five_in_database(self):
        for invalid_beds in (0, 6):
            with self.subTest(beds=invalid_beds), transaction.atomic():
                with self.assertRaises(IntegrityError):
                    Room.objects.create(
                        label=f"Room {invalid_beds}", beds=invalid_beds, view=Room.View.SEA
                    )

    def test_view_must_be_a_supported_value_in_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Room.objects.create(label="Invalid view", beds=1, view="city")

    def test_label_must_be_trimmed_in_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Room.objects.bulk_create(
                    [Room(label=" 101 ", beds=1, view=Room.View.SEA)]
                )

    def test_label_is_unique_without_case_sensitivity_in_database(self):
        Room.objects.create(label="101", beds=1, view=Room.View.SEA)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Room.objects.bulk_create(
                    [
                        Room(label="101A", beds=1, view=Room.View.SEA),
                        Room(label="101a", beds=1, view=Room.View.SEA),
                    ]
                )

    def test_save_trims_label_before_persisting(self):
        room = Room.objects.create(label=" 101 ", beds=1, view=Room.View.SEA)
        self.assertEqual(room.label, "101")
        self.assertEqual(Room.objects.get(pk=room.pk).label, "101")


class RoomAPITests(APITestCase):
    list_url = "/api/v1/rooms/"

    def create_room(self, label="101", beds=2, view=Room.View.SEA):
        return Room.objects.create(label=label, beds=beds, view=view)

    def test_delete_with_invalid_room_id_returns_json_404(self):
        room = self.create_room()

        response = self.client.delete(
            f"{self.list_url}invalid/", HTTP_ACCEPT="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["error"]["code"], "room_not_found")
        self.assertTrue(Room.objects.filter(pk=room.pk).exists())

    @override_settings(DEBUG=False)
    def test_unknown_api_path_returns_json_404(self):
        response = self.client.get("/api/v1/unknown/", HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    @override_settings(DEBUG=False)
    def test_unexpected_api_error_returns_generic_json_500(self):
        self.client.raise_request_exception = False
        with patch(
            "rooms.views.RoomViewSet.list", side_effect=RuntimeError("internal detail")
        ):
            response = self.client.get(self.list_url, HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "internal_server_error",
                    "message": "An internal server error occurred.",
                }
            },
        )

    def test_create_room_trims_label_and_returns_created_room(self):
        response = self.client.post(
            self.list_url,
            {"label": " 101 ", "beds": 2, "view": "sea"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["label"], "101")
        self.assertEqual(Room.objects.count(), 1)

    def test_room_label_with_apostrophe_and_wildcards_is_literal(self):
        self.create_room(label="O'BrienAB")
        label = "O'Brien%_"

        response = self.client.post(
            self.list_url, {"label": label, "beds": 2, "view": "sea"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Room.objects.get(label__iexact=label).pk, response.data["id"])
        self.assertEqual(Room.objects.count(), 2)

    def test_session_authenticated_room_write_requires_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(get_user_model().objects.create_user(username="room-user"))

        response = client.post(
            self.list_url, {"label": "101", "beds": 2, "view": "sea"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "permission_denied")
        self.assertFalse(Room.objects.exists())

    def test_create_room_from_html_form_with_csrf_token(self):
        form = self.client.get(self.list_url, HTTP_ACCEPT="text/html")
        self.assertContains(form, 'name="csrfmiddlewaretoken"')
        self.assertContains(form, 'value="yard"')
        csrf_token = form.cookies["csrftoken"].value

        for index, view in enumerate(("yard", "Yard")):
            with self.subTest(view=view):
                response = self.client.post(
                    self.list_url,
                    urlencode({
                        "label": f"Room {index}",
                        "beds": 2,
                        "view": view,
                        "csrfmiddlewaretoken": csrf_token,
                    }),
                    content_type="application/x-www-form-urlencoded",
                )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertEqual(response.data["view"], Room.View.YARD)
                self.assertEqual(
                    Room.objects.get(pk=response.data["id"]).view, Room.View.YARD
                )

    def test_create_rejects_invalid_beds_view_and_unknown_fields(self):
        invalid_payloads = [
            {"label": "101", "beds": 0, "view": "sea"},
            {"label": "101", "beds": 2, "view": "city"},
            {"label": "101", "beds": 2, "view": "sea", "unexpected": True},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(self.list_url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertEqual(Room.objects.count(), 0)

    def test_duplicate_label_is_a_conflict_case_insensitively(self):
        self.create_room(label="101A")

        response = self.client.post(
            self.list_url,
            {"label": "101a", "beds": 2, "view": "sea"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "duplicate_room_label")
        self.assertEqual(Room.objects.count(), 1)

    def test_update_duplicate_label_is_a_conflict(self):
        room = self.create_room(label="101")
        self.create_room(label="102")

        response = self.client.patch(
            f"{self.list_url}{room.pk}/", {"label": "102"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "duplicate_room_label")
        room.refresh_from_db()
        self.assertEqual(room.label, "101")

    def test_retrieve_update_and_delete_room(self):
        room = self.create_room()

        detail = self.client.get(f"{self.list_url}{room.pk}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["id"], room.pk)

        updated = self.client.patch(
            f"{self.list_url}{room.pk}/", {"beds": 3}, format="json"
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertEqual(updated.data["beds"], 3)

        deleted = self.client.delete(f"{self.list_url}{room.pk}/")
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())

    def test_put_replaces_all_writable_room_fields(self):
        room = self.create_room(label="101", beds=2, view=Room.View.SEA)

        response = self.client.put(
            f"{self.list_url}{room.pk}/",
            {"label": "101-renamed", "beds": 4, "view": "mountain"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["label"], "101-renamed")
        self.assertEqual(response.data["beds"], 4)
        self.assertEqual(response.data["view"], Room.View.MOUNTAIN)

    def test_missing_room_returns_standard_404_envelope(self):
        response = self.client.get(f"{self.list_url}99999/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "room_not_found")

    def test_filtering_and_pagination(self):
        self.create_room("101", beds=2, view=Room.View.SEA)
        self.create_room("102", beds=2, view=Room.View.SEA)
        self.create_room("201", beds=1, view=Room.View.MOUNTAIN)

        response = self.client.get(self.list_url, {"view": "sea", "beds": 2, "page_size": 1})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertIsNotNone(response.data["next"])

    def test_invalid_filters_and_pagination_return_400(self):
        for query in ({"view": "city"}, {"beds": 0}, {"page": 0}, {"page_size": 101}):
            with self.subTest(query=query):
                response = self.client.get(self.list_url, query)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["error"]["code"], "validation_error")

    def test_page_out_of_range_returns_404(self):
        self.create_room()

        response = self.client.get(self.list_url, {"page": 5})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "page_not_found")

    def test_booked_room_requires_confirmation_then_deletes_bookings(self):
        room = self.create_room()
        start = timezone.now() + timedelta(days=1)
        Booking.objects.create(
            room=room,
            user_id="user-42",
            check_in=start,
            check_out=start + timedelta(days=1),
        )

        initial_delete = self.client.delete(f"{self.list_url}{room.pk}/")
        self.assertEqual(initial_delete.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(initial_delete.data["error"]["code"], "confirmation_required")
        self.assertTrue(Room.objects.filter(pk=room.pk).exists())
        self.assertEqual(Booking.objects.filter(room=room).count(), 1)

        confirmed_delete = self.client.delete(f"{self.list_url}{room.pk}/?confirm=true")
        self.assertEqual(confirmed_delete.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())
        self.assertFalse(Booking.objects.filter(room_id=room.pk).exists())

