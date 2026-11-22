from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F
from django.db.models.functions import Trim
from django.utils import timezone

from rooms.models import Room
import uuid


class Booking(models.Model):
    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    user_id = models.CharField(max_length=128)
    check_in = models.DateTimeField()
    check_out = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(check_out__gt=F("check_in")),
                name="bookings_valid_time_range",
            ),
            models.CheckConstraint(
                check=models.Q(user_id=Trim("user_id")) & ~models.Q(user_id=""),
                name="bookings_user_id_trimmed",
            ),
        ]
        indexes = [
            models.Index(
                fields=["room", "check_in", "check_out"],
                name="bookings_room_dates_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.user_id is not None:
            self.user_id = self.user_id.strip()

        errors = {}
        if not self.user_id:
            errors["user_id"] = "User ID cannot be blank."
        if self.check_in and not timezone.is_aware(self.check_in):
            errors["check_in"] = "Check-in datetime must include a timezone."
        if self.check_out and not timezone.is_aware(self.check_out):
            errors["check_out"] = "Check-out datetime must include a timezone."
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            errors["check_out"] = "Check-out must be later than check-in."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.user_id is not None:
            self.user_id = self.user_id.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Booking {self.pk} — {self.room}"



class IdempotencyRecord(models.Model):
    """Durable snapshot of a successful booking request and its response."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"

    user_id = models.CharField(max_length=128)
    key = models.CharField(max_length=255)
    request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PROCESSING
    )
    # Intentionally not a ForeignKey: the saved response stays replayable even
    # when the original Booking is explicitly deleted under the room deletion policy.
    booking_id = models.BigIntegerField(null=True, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "key"],
                name="bookings_idempotency_user_key_uniq",
            ),
            # Never allow a completed record without a saved successful result.
            models.CheckConstraint(
                check=(
                    models.Q(
                        status="processing",
                        booking_id__isnull=True,
                        response_status__isnull=True,
                        response_body__isnull=True,
                    )
                    | models.Q(
                        status="completed",
                        booking_id__isnull=False,
                        response_status=201,
                        response_body__isnull=False,
                    )
                ),
                name="bookings_idempotency_valid_state",
            ),
        ]
