from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F
from django.db.models.functions import Trim
from django.utils import timezone

from rooms.models import Room


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
