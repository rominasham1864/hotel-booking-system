"""HTTP adapter for creating hotel room bookings (API v1)."""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from bookings.exceptions import InvalidBookingRequest, NoAvailableRoom
from bookings.serializers import BookingRequestSerializer, BookingResponseSerializer
from bookings.services import create_booking


def _error_response(*, code, message, status_code, fields=None):
    """Apply the documented API error envelope for expected booking errors."""
    error = {"code": code, "message": message}
    if fields is not None:
        error["fields"] = fields
    return Response({"error": error}, status=status_code)


class BookingCreateAPIView(APIView):
    """POST /api/v1/bookings/: validate input, delegate, serialize output."""

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

        try:
            # The service owns transaction.atomic and all booking business rules.
            booking = create_booking(**request_serializer.validated_data)
        except InvalidBookingRequest as error:
            return _error_response(
                code="validation_error",
                message=str(error),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except NoAvailableRoom as error:
            return _error_response(
                code="no_available_room",
                message=str(error),
                status_code=status.HTTP_409_CONFLICT,
            )

        return Response(
            BookingResponseSerializer(booking).data,
            status=status.HTTP_201_CREATED,
        )
