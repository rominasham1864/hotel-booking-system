"""Domain-level errors raised by the booking service."""


class BookingServiceError(Exception):
    """Base exception for expected booking failures."""


class InvalidBookingRequest(BookingServiceError):
    """Booking parameters violate the domain rules."""

    code = "validation_error"


class NoAvailableRoom(BookingServiceError):
    """No matching room is free for the requested period."""

    code = "no_available_room"

class IdempotencyKeyReused(BookingServiceError):
    """A key is reused with a different semantic booking request."""

    code = "idempotency_key_reused"


class IdempotencyRequestInProgress(BookingServiceError):
    """Another transaction still owns this user's idempotency key."""

    code = "idempotency_request_in_progress"
