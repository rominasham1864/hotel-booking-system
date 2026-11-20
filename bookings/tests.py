from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    skipUnlessDBFeature,
)

from bookings.exceptions import InvalidBookingRequest, NoAvailableRoom
from bookings.models import Booking
from bookings.services import create_booking, periods_overlap
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