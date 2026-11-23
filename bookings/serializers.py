"""HTTP input/output contracts for the booking endpoint.

Room selection and database writes intentionally live in bookings.services.
"""

from datetime import timezone as datetime_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from bookings.models import Booking
from rooms.models import Room
from rooms.serializers import RoomSerializer


class OffsetAwareDateTimeField(serializers.DateTimeField):
    """Require an explicit UTC offset in the incoming ISO 8601 timestamp.

    DRF can otherwise interpret a naive timestamp in the project's timezone,
    which contradicts this API's documented contract.
    """

    default_error_messages = {
        **serializers.DateTimeField.default_error_messages,
        "offset_required": "Date and time must include a UTC offset or Z.",
    }

    def __init__(self, **kwargs):
        kwargs.setdefault("default_timezone", datetime_timezone.utc)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                parsed_value = parse_datetime(data)
            except ValueError:
                parsed_value = None
            if parsed_value is not None and timezone.is_naive(parsed_value):
                self.fail("offset_required")
        elif hasattr(data, "tzinfo") and timezone.is_naive(data):
            self.fail("offset_required")

        value = super().to_internal_value(data)
        if timezone.is_naive(value):
            self.fail("offset_required")
        return value


class BookingRequestSerializer(serializers.Serializer):
    """Validate the booking request representation, without saving models."""

    user_id = serializers.CharField(max_length=128, trim_whitespace=True)
    beds = serializers.IntegerField(min_value=1, max_value=5)
    view = serializers.ChoiceField(choices=Room.View.choices)
    check_in = OffsetAwareDateTimeField()
    check_out = OffsetAwareDateTimeField()

    def to_internal_value(self, data):
        # DRF's default Serializer ignores extra keys. Our v1 contract does not.
        if isinstance(data, dict):
            unknown_fields = set(data) - set(self.fields)
            if unknown_fields:
                raise serializers.ValidationError(
                    {field: ["Unknown field."] for field in sorted(unknown_fields)}
                )
        return super().to_internal_value(data)

    def validate(self, attributes):
        if attributes["check_out"] <= attributes["check_in"]:
            raise serializers.ValidationError(
                {"check_out": ["check_out must be after check_in."]}
            )
        return attributes


class BookingResponseSerializer(serializers.ModelSerializer):
    """Return a booking with the selected room and UTC datetimes."""

    room = RoomSerializer(read_only=True)
    check_in = serializers.DateTimeField(
        read_only=True, default_timezone=datetime_timezone.utc
    )
    check_out = serializers.DateTimeField(
        read_only=True, default_timezone=datetime_timezone.utc
    )
    created_at = serializers.DateTimeField(
        read_only=True, default_timezone=datetime_timezone.utc
    )

    class Meta:
        model = Booking
        fields = ("id", "user_id", "check_in", "check_out", "created_at", "room")
        read_only_fields = fields
