from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from threading import Barrier, Event, Thread, current_thread
from unittest.mock import patch

from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.db.models.deletion import ProtectedError
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    skipUnlessDBFeature,
)

from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from bookings.exceptions import InvalidBookingRequest, NoAvailableRoom
from bookings.idempotency import semantic_payload_hash
from bookings.models import Booking, IdempotencyRecord
from bookings.services import OVERLAP_CONSTRAINT_NAME, create_booking, periods_overlap
from rooms.models import Room

@skipUnlessDBFeature("supports_table_check_constraints")
class BookingConstraintTests(TransactionTestCase):
    def setUp(self):
        self.room = Room.objects.create(label="101", beds=2, view=Room.View.SEA)
        self.check_in = datetime(2026, 7, 10, 10, 0, tzinfo=datetime_timezone.utc)

    def test_check_out_must_be_after_check_in_in_database(self):
        invalid_ranges = (self.check_in, datetime(2026, 7, 10, 9, 0, tzinfo=datetime_timezone.utc))
        for check_out in invalid_ranges:
            with self.subTest(check_out=check_out), transaction.atomic():
                with self.assertRaises(IntegrityError):
                    Booking.objects.create(
                        room=self.room,
                        user_id="user-42",
                        check_in=self.check_in,
                        check_out=check_out,
                    )

    def test_user_id_must_not_be_blank_or_padded_in_database(self):
        for user_id in ("", "   ", " user-42 "):
            with self.subTest(user_id=user_id), transaction.atomic():
                with self.assertRaises(IntegrityError):
                    Booking.objects.bulk_create(
                        [
                            Booking(
                                room=self.room,
                                user_id=user_id,
                                check_in=self.check_in,
                                check_out=datetime(
                                    2026, 7, 11, 10, 0, tzinfo=datetime_timezone.utc
                                ),
                            )
                        ]
                    )

    def test_room_foreign_key_uses_protect(self):
        Booking.objects.create(
            room=self.room,
            user_id="user-42",
            check_in=self.check_in,
            check_out=datetime(2026, 7, 11, 10, 0, tzinfo=datetime_timezone.utc),
        )
        with self.assertRaises(ProtectedError):
            self.room.delete()


START = datetime(2027, 1, 10, 14, 0, tzinfo=datetime_timezone.utc)
END = datetime(2027, 1, 12, 11, 0, tzinfo=datetime_timezone.utc)


class PeriodOverlapTests(SimpleTestCase):
    def test_intersecting_intervals_overlap(self):
        self.assertTrue(
            periods_overlap(START, END, START + timedelta(hours=1), END)
        )

    def test_adjacent_intervals_do_not_overlap(self):
        self.assertFalse(periods_overlap(START, END, END, END + timedelta(days=1)))
        self.assertFalse(periods_overlap(END, END + timedelta(days=1), START, END))

    def test_containing_intervals_overlap(self):
        self.assertTrue(
            periods_overlap(START, END, START - timedelta(days=1), END + timedelta(days=1))
        )

    def test_separate_intervals_do_not_overlap(self):
        self.assertFalse(
            periods_overlap(START, END, END + timedelta(hours=1), END + timedelta(days=1))
        )


class BookingServiceTests(TestCase):
    def setUp(self):
        self.first_room = Room.objects.create(label="101", beds=2, view="sea")
        self.second_room = Room.objects.create(label="102", beds=2, view="sea")
        Room.objects.create(label="103", beds=3, view="mountain")

    def book(self, **overrides):
        request = {
            "user_id": "user-42",
            "beds": 2,
            "view": "sea",
            "check_in": START,
            "check_out": END,
        }
        request.update(overrides)
        return create_booking(**request)

    def add_existing_booking(self, room, check_in=START, check_out=END):
        return Booking.objects.create(
            room=room,
            user_id="existing-user",
            check_in=check_in,
            check_out=check_out,
        )

    def test_creates_booking_in_lowest_id_room(self):
        booking = self.book(user_id="  user-42  ")
        self.assertEqual(booking.room_id, self.first_room.pk)
        self.assertEqual(booking.user_id, "user-42")
        self.assertEqual(Booking.objects.count(), 1)

    def test_uses_next_available_matching_room(self):
        self.add_existing_booking(self.first_room)
        booking = self.book()
        self.assertEqual(booking.room_id, self.second_room.pk)

    def test_returns_no_available_room_when_all_matching_rooms_conflict(self):
        self.add_existing_booking(self.first_room)
        self.add_existing_booking(self.second_room)
        with self.assertRaises(NoAvailableRoom):
            self.book()
        self.assertEqual(Booking.objects.count(), 2)

    def test_returns_no_available_room_when_features_do_not_match(self):
        with self.assertRaises(NoAvailableRoom):
            self.book(beds=5)
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_may_start_exactly_at_previous_checkout(self):
        self.add_existing_booking(self.first_room)
        booking = self.book(check_in=END, check_out=END + timedelta(days=1))
        self.assertEqual(booking.room_id, self.first_room.pk)

    def test_booking_may_end_exactly_at_next_checkin(self):
        self.add_existing_booking(self.first_room)
        booking = self.book(
            check_in=START - timedelta(days=1),
            check_out=START,
        )
        self.assertEqual(booking.room_id, self.first_room.pk)

    def test_gap_between_multiple_existing_bookings_is_available(self):
        """Both overlap predicates must refer to the SAME existing booking."""
        self.add_existing_booking(
            self.first_room,
            check_in=START - timedelta(days=3),
            check_out=START - timedelta(days=2),
        )
        self.add_existing_booking(
            self.first_room,
            check_in=END + timedelta(days=2),
            check_out=END + timedelta(days=3),
        )
        booking = self.book()
        self.assertEqual(booking.room_id, self.first_room.pk)

    def test_overlapping_booking_goes_to_next_room(self):
        self.add_existing_booking(
            self.first_room,
            check_in=START + timedelta(hours=1),
            check_out=START + timedelta(days=1),
        )
        booking = self.book()
        self.assertEqual(booking.room_id, self.second_room.pk)

    def test_rejects_equal_or_reversed_period_without_writing(self):
        for check_out in (START, START - timedelta(hours=1)):
            with self.subTest(check_out=check_out):
                with self.assertRaises(InvalidBookingRequest):
                    self.book(check_out=check_out)
        self.assertEqual(Booking.objects.count(), 0)

    def test_rejects_naive_datetime(self):
        with self.assertRaises(InvalidBookingRequest):
            self.book(check_in=START.replace(tzinfo=None))

    def test_rejects_invalid_room_features(self):
        for field, value in (("beds", 0), ("beds", True), ("view", "ocean")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(InvalidBookingRequest):
                    self.book(**{field: value})
        self.assertEqual(Booking.objects.count(), 0)

    def test_rejects_blank_user_id(self):
        with self.assertRaises(InvalidBookingRequest):
            self.book(user_id="   ")

    def test_normalizes_offset_aware_timestamps_to_utc(self):
        iran_timezone = datetime_timezone(
            timedelta(hours=3, minutes=30)
        )

        booking = self.book(
            check_in=START.astimezone(iran_timezone),
            check_out=END.astimezone(iran_timezone),
        )

        self.assertEqual(booking.check_in, START)
        self.assertEqual(booking.check_out, END)
        self.assertEqual(booking.check_in.utcoffset(), timedelta(0))


BOOKINGS_URL = "/api/v1/bookings/"
CHECK_IN = datetime(2027, 1, 10, 14, 0, tzinfo=datetime_timezone.utc)
CHECK_OUT = datetime(2027, 1, 12, 11, 0, tzinfo=datetime_timezone.utc)


class BookingCreateAPITests(APITestCase):
    def setUp(self):
        self.first_room = Room.objects.create(label="101", beds=2, view="sea")
        self.second_room = Room.objects.create(label="102", beds=2, view="sea")
        self.valid_payload = {
            "user_id": "user-42",
            "beds": 2,
            "view": "sea",
            "check_in": CHECK_IN.isoformat(),
            "check_out": CHECK_OUT.isoformat(),
        }

    def post_booking(self, payload=None, *, key="phase6-request-1"):
        return self.client.post(
            BOOKINGS_URL,
            data=self.valid_payload if payload is None else payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_valid_request_creates_booking_and_returns_room(self):
        payload = {**self.valid_payload, "user_id": " user-42 "}
        response = self.post_booking(payload)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(response.data["user_id"], "user-42")
        self.assertEqual(response.data["room"]["id"], self.first_room.id)
        self.assertEqual(response.data["room"]["label"], "101")
        self.assertIn("created_at", response.data)
        self.assertTrue(response.data["check_in"].endswith("Z"))
        self.assertTrue(response.data["check_out"].endswith("Z"))

    def test_selects_next_free_room(self):
        Booking.objects.create(
            room=self.first_room,
            user_id="existing-user",
            check_in=CHECK_IN,
            check_out=CHECK_OUT,
        )
        response = self.post_booking()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["room"]["id"], self.second_room.id)

    def test_no_available_room_returns_409_and_does_not_write(self):
        for room in (self.first_room, self.second_room):
            Booking.objects.create(
                room=room,
                user_id="existing-user",
                check_in=CHECK_IN,
                check_out=CHECK_OUT,
            )
        response = self.post_booking()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "no_available_room")
        self.assertEqual(Booking.objects.count(), 2)

    def test_unmatched_features_return_409_and_do_not_write(self):
        response = self.post_booking({**self.valid_payload, "beds": 5})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Booking.objects.count(), 0)

    def test_missing_required_field_returns_400(self):
        payload = dict(self.valid_payload)
        payload.pop("check_in")
        response = self.post_booking(payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("check_in", response.data["error"]["fields"])
        self.assertEqual(Booking.objects.count(), 0)

    def test_invalid_room_features_return_400(self):
        for field, value in (("beds", 0), ("beds", True), ("view", "ocean")):
            with self.subTest(field=field, value=value):
                response = self.post_booking({**self.valid_payload, field: value})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data["error"]["fields"])
        self.assertEqual(Booking.objects.count(), 0)

    def test_unknown_request_field_is_rejected(self):
        response = self.post_booking({**self.valid_payload, "room_id": 123})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("room_id", response.data["error"]["fields"])
        self.assertEqual(Booking.objects.count(), 0)

    def test_equal_or_reversed_date_range_returns_400(self):
        for check_out in (CHECK_IN, CHECK_IN - timedelta(hours=1)):
            with self.subTest(check_out=check_out):
                response = self.post_booking(
                    {**self.valid_payload, "check_out": check_out.isoformat()}
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("check_out", response.data["error"]["fields"])
        self.assertEqual(Booking.objects.count(), 0)

    def test_naive_timestamp_is_rejected(self):
        response = self.post_booking(
            {**self.valid_payload, "check_in": "2027-01-10T14:00:00"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("check_in", response.data["error"]["fields"])
        self.assertEqual(Booking.objects.count(), 0)

    def test_offset_timestamp_is_returned_as_utc(self):
        iran_timezone = datetime_timezone(timedelta(hours=3, minutes=30))
        payload = {
            **self.valid_payload,
            "check_in": CHECK_IN.astimezone(iran_timezone).isoformat(),
            "check_out": CHECK_OUT.astimezone(iran_timezone).isoformat(),
        }
        response = self.post_booking(payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["check_in"], "2027-01-10T14:00:00Z")
        self.assertEqual(response.data["check_out"], "2027-01-12T11:00:00Z")

    def test_missing_idempotency_key_returns_400(self):
        response = self.client.post(BOOKINGS_URL, self.valid_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Idempotency-Key", response.data["error"]["fields"])
        self.assertEqual(Booking.objects.count(), 0)

    def test_invalid_idempotency_key_returns_400(self):
        for key in ("   ", "x" * 256):
            with self.subTest(key_length=len(key)):
                response = self.post_booking(key=key)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Booking.objects.count(), 0)

    def test_empty_user_id_is_rejected(self):
        response = self.post_booking({**self.valid_payload, "user_id": "  "})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Booking.objects.count(), 0)

    def test_domain_validation_error_maps_to_400(self):
        with patch(
            "bookings.views.create_booking",
            side_effect=InvalidBookingRequest("Invalid booking period."),
        ):
            response = self.post_booking()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertEqual(Booking.objects.count(), 0)

    def test_get_not_supported(self):
        response = self.client.get(BOOKINGS_URL)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class BookingConcurrencyTests(TransactionTestCase):
    """TransactionTestCase permits real commits across separate connections."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Booking concurrency tests require PostgreSQL.")
        self.check_in = datetime(2027, 2, 10, 14, 0, tzinfo=datetime_timezone.utc)
        self.check_out = datetime(2027, 2, 12, 11, 0, tzinfo=datetime_timezone.utc)
        self.first_room = Room.objects.create(label="101", beds=2, view="sea")

    def _book(self, *, user_id: str):
        return create_booking(
            user_id=user_id,
            beds=2,
            view="sea",
            check_in=self.check_in,
            check_out=self.check_out,
        )

    def _run_two_requests(self):
        """Synchronize starts, with one DB connection per worker thread."""
        gate = Barrier(3)

        def worker(user_id):
            close_old_connections()
            try:
                gate.wait(timeout=15)
                try:
                    booking = self._book(user_id=user_id)
                except NoAvailableRoom:
                    return ("unavailable", None)
                return ("created", booking.room_id)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(worker, "user-A")
            second = executor.submit(worker, "user-B")
            gate.wait(timeout=15)
            return [first.result(timeout=30), second.result(timeout=30)]

    def test_two_simultaneous_requests_cannot_double_book_single_room(self):
        results = self._run_two_requests()
        self.assertEqual(sorted(kind for kind, _ in results), ["created", "unavailable"])
        self.assertEqual(Booking.objects.count(), 1)

    def test_simultaneous_requests_use_distinct_rooms_if_available(self):
        second_room = Room.objects.create(label="102", beds=2, view="sea")
        results = self._run_two_requests()
        self.assertEqual([kind for kind, _ in results].count("created"), 2)
        self.assertEqual(
            {room_id for kind, room_id in results},
            {self.first_room.pk, second_room.pk},
        )
        self.assertEqual(Booking.objects.count(), 2)

    def test_direct_model_create_cannot_bypass_overlap_constraint(self):
        self._book(user_id="user-A")
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                Booking.objects.create(
                    room=self.first_room,
                    user_id="user-B",
                    check_in=self.check_in + timedelta(hours=2),
                    check_out=self.check_out,
                )
        diagnostic = getattr(raised.exception.__cause__, "diag", None)
        self.assertEqual(getattr(diagnostic, "constraint_name", None), OVERLAP_CONSTRAINT_NAME)
        self.assertEqual(Booking.objects.count(), 1)

    def test_adjacent_reservations_are_allowed_by_constraint(self):
        self._book(user_id="user-A")
        Booking.objects.create(
            room=self.first_room,
            user_id="user-B",
            check_in=self.check_out,
            check_out=self.check_out + timedelta(days=1),
        )
        self.assertEqual(Booking.objects.count(), 2)

    def test_direct_update_cannot_create_overlap(self):
        self._book(user_id="user-A")
        later_booking = Booking.objects.create(
            room=self.first_room,
            user_id="user-B",
            check_in=self.check_out,
            check_out=self.check_out + timedelta(days=2),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Booking.objects.filter(pk=later_booking.pk).update(
                    check_in=self.check_in + timedelta(hours=1),
                )
        later_booking.refresh_from_db()
        self.assertEqual(later_booking.check_in, self.check_out)

    def test_outer_transaction_rolls_back_service_write(self):
        with self.assertRaisesRegex(RuntimeError, "simulate failure"):
            with transaction.atomic():
                self._book(user_id="user-A")
                raise RuntimeError("simulate failure")
        self.assertEqual(Booking.objects.count(), 0)

    def test_constraint_is_installed_in_postgres(self):
        with connection.cursor() as cursor:
            db_constraints = connection.introspection.get_constraints(
                cursor, Booking._meta.db_table
            )
        self.assertIn(OVERLAP_CONSTRAINT_NAME, db_constraints)


IDEMPOTENCY_CHECK_IN = datetime(2027, 3, 10, 14, 0, tzinfo=datetime_timezone.utc)
IDEMPOTENCY_CHECK_OUT = datetime(2027, 3, 12, 11, 0, tzinfo=datetime_timezone.utc)


def booking_payload(*, user_id="user-42"):
    return {
        "user_id": user_id,
        "beds": 2,
        "view": "sea",
        "check_in": IDEMPOTENCY_CHECK_IN.isoformat(),
        "check_out": IDEMPOTENCY_CHECK_OUT.isoformat(),
    }


class BookingIdempotencyAPITests(APITestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Phase 8 uses PostgreSQL advisory locks.")
        Room.objects.create(label="101", beds=2, view="sea")
        Room.objects.create(label="102", beds=2, view="sea")

    def post(self, payload=None, *, key="request-001"):
        return self.client.post(
            BOOKINGS_URL,
            data=booking_payload() if payload is None else payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_first_request_creates_booking_and_completed_record(self):
        response = self.post()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Booking.objects.count(), 1)
        record = IdempotencyRecord.objects.get()
        self.assertEqual(record.user_id, "user-42")
        self.assertEqual(record.key, "request-001")
        self.assertEqual(record.status, IdempotencyRecord.Status.COMPLETED)
        self.assertEqual(record.booking_id, response.data["id"])
        self.assertEqual(record.response_status, 201)
        self.assertEqual(record.response_body, response.data)
        self.assertIsNotNone(record.request_id)

    def test_same_key_and_same_payload_replays_original_response(self):
        first = self.post()
        second = self.post()
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.data, second.data)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_replay_retains_response_snapshot_after_room_changes(self):
        first = self.post()
        Room.objects.filter(pk=first.data["room"]["id"]).update(label="renamed")
        replay = self.post()
        self.assertEqual(replay.data, first.data)
        self.assertEqual(replay.data["room"]["label"], "101")

    def test_identical_instant_with_different_utc_offset_is_same_payload(self):
        first = self.post()
        offset = datetime_timezone(timedelta(hours=3, minutes=30))
        second_payload = booking_payload()
        second_payload["check_in"] = IDEMPOTENCY_CHECK_IN.astimezone(offset).isoformat()
        second_payload["check_out"] = IDEMPOTENCY_CHECK_OUT.astimezone(offset).isoformat()
        second = self.post(second_payload)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.data, first.data)
        self.assertEqual(Booking.objects.count(), 1)

    def test_trimmed_user_id_is_same_semantic_payload(self):
        first = self.post()
        second = self.post({**booking_payload(), "user_id": "  user-42  "})
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.data, first.data)
        self.assertEqual(Booking.objects.count(), 1)

    def test_reusing_same_key_with_different_payload_is_409(self):
        self.post()
        response = self.post({**booking_payload(), "beds": 3})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "idempotency_key_reused")
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_same_key_is_scoped_to_user_id(self):
        first = self.post()
        second = self.post(booking_payload(user_id="user-99"))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(Booking.objects.count(), 2)
        self.assertEqual(IdempotencyRecord.objects.count(), 2)
        self.assertNotEqual(first.data["room"]["id"], second.data["room"]["id"])

    def test_no_availability_does_not_persist_idempotency_record(self):
        # The request cannot reserve a matching 5-bed room yet.
        payload = {**booking_payload(), "beds": 5}
        unavailable = self.post(payload)
        self.assertEqual(unavailable.status_code, 409)
        self.assertEqual(unavailable.data["error"]["code"], "no_available_room")
        self.assertEqual(IdempotencyRecord.objects.count(), 0)
        Room.objects.create(label="105", beds=5, view="sea")
        retry = self.post(payload)
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        self.assertEqual(Booking.objects.count(), 1)

    def test_invalid_payload_does_not_persist_record(self):
        response = self.post({**booking_payload(), "beds": 0})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)
        self.assertEqual(Booking.objects.count(), 0)

    def test_failed_response_serialization_rolls_back_both_writes(self):
        with patch(
            "bookings.views.BookingResponseSerializer",
            side_effect=RuntimeError("response serialization failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "serialization failed"):
                self.post()
        self.assertEqual(IdempotencyRecord.objects.count(), 0)
        self.assertEqual(Booking.objects.count(), 0)

    def test_database_unique_key_prevents_duplicate_record_bypass(self):
        self.post()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IdempotencyRecord.objects.create(
                    user_id="user-42",
                    key="request-001",
                    request_hash="0" * 64,
                )
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_existing_processing_record_returns_in_progress(self):
        semantic_data = {
            "user_id": "user-42",
            "beds": 2,
            "view": "sea",
            "check_in": IDEMPOTENCY_CHECK_IN,
            "check_out": IDEMPOTENCY_CHECK_OUT,
        }
        IdempotencyRecord.objects.create(
            user_id="user-42",
            key="request-001",
            request_hash=semantic_payload_hash(semantic_data),
            status=IdempotencyRecord.Status.PROCESSING,
        )
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "idempotency_request_in_progress")
        self.assertEqual(Booking.objects.count(), 0)


class BookingIdempotencyConcurrencyTests(TransactionTestCase):
    """Two requests use distinct real database connections."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Phase 8 concurrency tests require PostgreSQL.")
        Room.objects.create(label="101", beds=2, view="sea")
        Room.objects.create(label="102", beds=2, view="sea")

    def test_concurrent_same_key_returns_in_progress_then_replays(self):
        started, release = Event(), Event()
        outcome = {}

        def slow_create_booking(**kwargs):
            if current_thread().name == "idempotency-holder":
                started.set()  # Advisory lock is held by this point.
                if not release.wait(timeout=15):
                    raise RuntimeError("Test timed out waiting to release booking")
            return create_booking(**kwargs)

        def first_request():
            close_old_connections()
            try:
                client = APIClient()
                response = client.post(
                    BOOKINGS_URL,
                    data=booking_payload(),
                    format="json",
                    HTTP_IDEMPOTENCY_KEY="same-key",
                )
                outcome["status"] = response.status_code
                outcome["body"] = response.data
            except Exception as error:  # surface errors from the worker thread
                outcome["error"] = error
            finally:
                connections.close_all()

        with patch("bookings.views.create_booking", side_effect=slow_create_booking):
            thread = Thread(target=first_request, name="idempotency-holder", daemon=True)
            thread.start()
            try:
                self.assertTrue(started.wait(timeout=10), "First request did not enter booking")
                duplicate = APIClient().post(
                    BOOKINGS_URL,
                    data=booking_payload(),
                    format="json",
                    HTTP_IDEMPOTENCY_KEY="same-key",
                )
                self.assertEqual(duplicate.status_code, 409)
                self.assertEqual(
                    duplicate.data["error"]["code"],
                    "idempotency_request_in_progress",
                )
            finally:
                release.set()
                thread.join(timeout=20)

        self.assertFalse(thread.is_alive(), "First request did not finish")
        if "error" in outcome:
            raise outcome["error"]
        self.assertEqual(outcome["status"], 201)
        replay = APIClient().post(
            BOOKINGS_URL, data=booking_payload(), format="json", HTTP_IDEMPOTENCY_KEY="same-key"
        )
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.data, outcome["body"])
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
