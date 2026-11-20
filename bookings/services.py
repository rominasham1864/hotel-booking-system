"""Booking business logic, independent from HTTP views and serializers.

Phase 5 implementation. Concurrent requests are not yet protected from
selecting the same room; Phase 7 adds locking and an exclusion constraint.
"""

from datetime import datetime, timezone as datetime_timezone
from typing import Optional

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from bookings.exceptions import InvalidBookingRequest, NoAvailableRoom
from bookings.models import Booking
from rooms.models import Room

VALID_ROOM_VIEWS = frozenset({"sea", "mountain", "yard"})


def periods_overlap(
    existing_check_in: datetime,
    existing_check_out: datetime,
    requested_check_in: datetime,
    requested_check_out: datetime,
) -> bool:
    """Check overlap of two valid half-open intervals: [start, end)."""
    return (
        existing_check_in < requested_check_out
        and requested_check_in < existing_check_out
    )


def _validate_booking_request(
    *,
    user_id: str,
    beds: int,
    view: str,
    check_in: datetime,
    check_out: datetime,
) -> str:
    """Validate the domain contract and return the normalized user ID."""
    if not isinstance(user_id, str) or not 1 <= len(user_id.strip()) <= 128:
        raise InvalidBookingRequest("user_id must contain 1 to 128 characters.")

    if type(beds) is not int or not 1 <= beds <= 5:
        raise InvalidBookingRequest("beds must be an integer between 1 and 5.")

    if not isinstance(view, str) or view not in VALID_ROOM_VIEWS:
        raise InvalidBookingRequest("view must be sea, mountain, or yard.")

    if (
        not isinstance(check_in, datetime)
        or not isinstance(check_out, datetime)
        or timezone.is_naive(check_in)
        or timezone.is_naive(check_out)
    ):
        raise InvalidBookingRequest(
            "check_in and check_out must be timezone-aware datetimes."
        )

    if check_out <= check_in:
        raise InvalidBookingRequest("check_out must be after check_in.")

    return user_id.strip()


def find_available_room(
    *, beds: int, view: str, check_in: datetime, check_out: datetime
) -> Optional[Room]:
    """Return the lowest-ID matching room without any conflicting booking.

    NOT EXISTS binds both overlap predicates to the same booking row.
    This avoids an incorrect match when a room has several past/future bookings.
    """
    conflicting_bookings = Booking.objects.filter(
        room_id=OuterRef("pk"),
        check_in__lt=check_out,
        check_out__gt=check_in,
    )

    return (
        Room.objects.filter(beds=beds, view=view)
        .filter(~Exists(conflicting_bookings))
        .order_by("id")
        .first()
    )


@transaction.atomic
def create_booking(
    *,
    user_id: str,
    beds: int,
    view: str,
    check_in: datetime,
    check_out: datetime,
) -> Booking:
    """Validate, select a free room deterministically, and create a booking.

    Atomicity alone does NOT prevent two concurrent requests from selecting
    the same room. Phase 7 must add database locking and an exclusion constraint.
    """
    normalized_user_id = _validate_booking_request(
        user_id=user_id,
        beds=beds,
        view=view,
        check_in=check_in,
        check_out=check_out,
    )
    check_in_utc = check_in.astimezone(datetime_timezone.utc)
    check_out_utc = check_out.astimezone(datetime_timezone.utc)

    room = find_available_room(
        beds=beds,
        view=view,
        check_in=check_in_utc,
        check_out=check_out_utc,
    )
    if room is None:
        raise NoAvailableRoom("No matching room is available for this period.")

    return Booking.objects.create(
        room=room,
        user_id=normalized_user_id,
        check_in=check_in_utc,
        check_out=check_out_utc,
    )
