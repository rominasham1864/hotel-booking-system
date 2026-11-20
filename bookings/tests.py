from datetime import datetime, timezone as datetime_timezone

from django.db.models.deletion import ProtectedError
from django.db import IntegrityError, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature

from bookings.models import Booking
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
