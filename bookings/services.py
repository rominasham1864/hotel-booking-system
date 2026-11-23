"""Hotel booking business rules.

The read-only find_available_room() is a snapshot, not a concurrency guarantee.
Only create_booking() performs a transaction, row locks, and a post-lock check.
PostgreSQL's exclusion constraint is the final invariant against overlaps.
"""

from datetime import datetime, timezone as datetime_timezone
from typing import Optional

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from bookings.exceptions import InvalidBookingRequest, NoAvailableRoom
from bookings.models import Booking
from rooms.models import Room

VALID_ROOM_VIEWS = frozenset({"sea", "mountain", "yard"})
OVERLAP_CONSTRAINT_NAME = "bookings_no_overlap"


def periods_overlap(
    existing_check_in: datetime,
    existing_check_out: datetime,
    requested_check_in: datetime,
    requested_check_out: datetime,
) -> bool:
    """Whether two valid [start, end) periods overlap."""
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
    """Enforce service-level domain rules even for non-HTTP callers."""
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
    """Read-only availability snapshot, NOT safe for reserving concurrently.

    Both overlap predicates are evaluated against the same Booking row.
    Call create_booking() to make a reservation.
    """
    conflicting_bookings = Booking.objects.filter(
        room_id=OuterRef("pk"),
        check_in__lt=check_out,
        check_out__gt=check_in,
    )

    return (
        Room.objects.filter(beds=beds, view=view)
        .filter(~Exists(conflicting_bookings))
        .order_by("pk")
        .first()
    )


def _constraint_name(error: IntegrityError) -> Optional[str]:
    """Extract the PostgreSQL constraint name (psycopg2/psycopg3)."""
    diagnostics = getattr(error.__cause__, "diag", None)
    return getattr(diagnostics, "constraint_name", None)


@transaction.atomic
def create_booking(
    *,
    user_id: str,
    beds: int,
    view: str,
    check_in: datetime,
    check_out: datetime,
) -> Booking:
    """Create a booking without allowing concurrent overlapping reservations.

    Candidate IDs are considered in ascending order. Each candidate is locked
    first and checked for conflicts *after* the lock is acquired. Only the
    candidate being checked is locked, instead of locking every matching room.
    PostgreSQL's exclusion constraint protects against writers that bypass this
    service. An exclusion violation on one candidate retries the next room.

    This design assumes PostgreSQL's usual READ COMMITTED isolation level.
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

    candidate_ids = (
        Room.objects.filter(beds=beds, view=view)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    for room_id in candidate_ids:
        try:
            # An inner savepoint lets us recover from the *specific* constraint
            # error without leaving the outer transaction in a broken state.
            with transaction.atomic():
                room = (
                    Room.objects.select_for_update()
                    .filter(pk=room_id, beds=beds, view=view)
                    .first()
                )
                if room is None:
                    # Room was deleted or its properties changed in the meantime.
                    continue

                conflict_exists = Booking.objects.filter(
                    room_id=room.pk,
                    check_in__lt=check_out_utc,
                    check_out__gt=check_in_utc,
                ).exists()
                if conflict_exists:
                    continue

                return Booking.objects.create(
                    room=room,
                    user_id=normalized_user_id,
                    check_in=check_in_utc,
                    check_out=check_out_utc,
                )
        except IntegrityError as error:
            if _constraint_name(error) == OVERLAP_CONSTRAINT_NAME:
                # A non-cooperating writer can insert without locking Room.
                # Try another candidate rather than returning a raw HTTP 500.
                continue
            # Never hide unrelated integrity failures as "no room available".
            raise

    raise NoAvailableRoom("No matching room is available for this period.")
