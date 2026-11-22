"""Idempotent booking orchestration, independent of DRF serialization.

PostgreSQL-specific: a transaction-scoped advisory lock prevents another
request using the same (user_id, Idempotency-Key) from running concurrently.
A unique DB constraint is the durable guarantee for completed records.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from typing import Any, Callable, Mapping

from django.db import connection, transaction
from django.utils import timezone

from bookings.exceptions import (
    IdempotencyKeyReused,
    IdempotencyRequestInProgress,
    InvalidBookingRequest,
)
from bookings.models import Booking, IdempotencyRecord


@dataclass(frozen=True)
class IdempotencyResult:
    status_code: int
    body: dict[str, Any]
    request_id: str
    replayed: bool


def _utc_timestamp(value: datetime) -> str:
    """Normalize identical instants with different offsets to one string."""
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise InvalidBookingRequest("Booking datetimes must have explicit UTC offsets.")
    return value.astimezone(datetime_timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def semantic_payload_hash(booking_data: Mapping[str, Any]) -> str:
    """Fingerprint the *validated*, semantically normalized booking request."""
    canonical = {
        "user_id": booking_data["user_id"].strip(),
        "beds": booking_data["beds"],
        "view": booking_data["view"],
        "check_in": _utc_timestamp(booking_data["check_in"]),
        "check_out": _utc_timestamp(booking_data["check_out"]),
    }
    encoded = json.dumps(
        canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _advisory_lock_id(*, user_id: str, idempotency_key: str) -> int:
    """Derive a stable signed bigint for PostgreSQL advisory locking.

    An extremely unlikely hash-prefix collision may briefly reject an unrelated
    request as in-progress, but never makes its saved result accessible.
    """
    lock_name = json.dumps(
        ["hotel-booking:idempotency:v1", user_id, idempotency_key],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(lock_name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _try_acquire_idempotency_lock(*, user_id: str, idempotency_key: str) -> bool:
    if connection.vendor != "postgresql":
        raise RuntimeError("Booking idempotency requires PostgreSQL.")
    lock_id = _advisory_lock_id(user_id=user_id, idempotency_key=idempotency_key)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [lock_id])
        (acquired,) = cursor.fetchone()
    return bool(acquired)


def create_idempotent_booking(
    *,
    idempotency_key: str,
    booking_data: Mapping[str, Any],
    create_booking_fn: Callable[..., Booking],
    serialize_booking_fn: Callable[[Booking], Mapping[str, Any]],
) -> IdempotencyResult:
    """Create once or replay the saved HTTP result for the same semantic request.

    Booking and successful response record commit together. No-availability or
    unexpected failures roll back the pending record and the booking.

    ``create_booking_fn`` and ``serialize_booking_fn`` are injected so the
    orchestrator does not depend on a particular DRF serializer or HTTP view.
    """
    if (
        not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 255
        or not idempotency_key.strip()
    ):
        raise InvalidBookingRequest("Invalid Idempotency-Key header.")

    # RequestSerializer and create_booking() already validate this input.
    # User ID is normalized here too, making the scope consistent with the DB.
    user_id = booking_data["user_id"].strip()
    request_hash = semantic_payload_hash(booking_data)

    with transaction.atomic():
        if not _try_acquire_idempotency_lock(
            user_id=user_id, idempotency_key=idempotency_key
        ):
            raise IdempotencyRequestInProgress(
                "A booking request with this Idempotency-Key is still processing."
            )

        record = IdempotencyRecord.objects.filter(
            user_id=user_id, key=idempotency_key
        ).first()
        if record is not None:
            if record.request_hash != request_hash:
                raise IdempotencyKeyReused(
                    "This Idempotency-Key was already used with different booking details."
                )
            if record.status != IdempotencyRecord.Status.COMPLETED:
                raise IdempotencyRequestInProgress(
                    "A booking request with this Idempotency-Key is still processing."
                )
            return IdempotencyResult(
                status_code=record.response_status,
                body=record.response_body,
                request_id=str(record.request_id),
                replayed=True,
            )

        record = IdempotencyRecord.objects.create(
            user_id=user_id,
            key=idempotency_key,
            request_hash=request_hash,
            status=IdempotencyRecord.Status.PROCESSING,
        )
        # Phase 7 create_booking() manages Room row locks and exclusion errors.
        # Its atomic() is nested here, so the booking cannot commit separately
        # from the idempotency response record.
        booking = create_booking_fn(**booking_data)
        serialized = dict(serialize_booking_fn(booking))
        # Force an actual JSON snapshot now, before committing the transaction.
        # If serialization fails, both writes roll back.
        body = json.loads(json.dumps(serialized, ensure_ascii=False))

        record.booking_id = booking.pk
        record.response_status = 201
        record.response_body = body
        record.status = IdempotencyRecord.Status.COMPLETED
        record.save(
            update_fields=[
                "booking_id", "response_status", "response_body", "status", "updated_at"
            ]
        )
        return IdempotencyResult(
            status_code=201,
            body=body,
            request_id=str(record.request_id),
            replayed=False,
        )
