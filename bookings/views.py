"""Booking HTTP adapter for phases 6-8 (merged version)."""

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from bookings.exceptions import (
    IdempotencyKeyReused,
    IdempotencyRequestInProgress,
    InvalidBookingRequest,
    NoAvailableRoom,
)
from bookings.idempotency import create_idempotent_booking
from bookings.serializers import BookingRequestSerializer, BookingResponseSerializer
from bookings.services import create_booking
logger = logging.getLogger("hotel.bookings")


def _error_response(*, code, message, status_code, fields=None):
    error = {"code": code, "message": message}
    if fields is not None:
        error["fields"] = fields
    return Response({"error": error}, status=status_code)


class BookingCreateAPIView(APIView):
    """POST /api/v1/bookings/: make the booking with idempotent retries."""

    def post(self, request):
        idempotency_key = request.headers.get("Idempotency-Key")
        if (
            idempotency_key is None
            or not 1 <= len(idempotency_key) <= 255
            or not idempotency_key.strip()
        ):
            return _error_response(
                code="validation_error",
                message="Request validation failed.",
                status_code=status.HTTP_400_BAD_REQUEST,
                fields={"Idempotency-Key": ["Must contain 1 to 255 non-blank characters."]},
            )

        request_serializer = BookingRequestSerializer(data=request.data)
        if not request_serializer.is_valid():
            return _error_response(
                code="validation_error",
                message="Request validation failed.",
                status_code=status.HTTP_400_BAD_REQUEST,
                fields=request_serializer.errors,
            )

        request_fields = {"request_id": getattr(request, "request_id", None)}
        try:
            result = create_idempotent_booking(
                idempotency_key=idempotency_key,
                booking_data=request_serializer.validated_data,
                create_booking_fn=create_booking,
                serialize_booking_fn=lambda booking: BookingResponseSerializer(booking).data,
            )
        except InvalidBookingRequest as error:
            return _error_response(
                code="validation_error",
                message=str(error),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except NoAvailableRoom as error:
            logger.info("booking.unavailable", extra=request_fields)
            return _error_response(
                code="no_available_room",
                message=str(error),
                status_code=status.HTTP_409_CONFLICT,
            )
        except (IdempotencyKeyReused, IdempotencyRequestInProgress) as error:
            logger.info(
                "idempotency.rejected",
                extra={**request_fields, "error_code": error.code},
            )
            return _error_response(
                code=error.code,
                message=str(error),
                status_code=status.HTTP_409_CONFLICT,
            )

        logger.info(
            "idempotency.replayed" if result.replayed else "booking.created",
            extra={
                **request_fields,
                "booking_id": result.booking_id,
                "idempotency_request_id": result.request_id,
                "idempotency_outcome": "replayed" if result.replayed else "created",
            },
        )
        return Response(result.body, status=result.status_code)
