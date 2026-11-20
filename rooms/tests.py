from django.db import IntegrityError, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature

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

