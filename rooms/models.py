from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower, Trim


class Room(models.Model):
    class View(models.TextChoices):
        SEA = "sea", "Sea"
        MOUNTAIN = "mountain", "Mountain"
        YARD = "yard", "Yard"

    label = models.CharField(max_length=100)
    beds = models.PositiveSmallIntegerField()
    view = models.CharField(max_length=10, choices=View.choices)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                Lower("label"),
                name="rooms_room_label_ci_uniq",
            ),
            models.CheckConstraint(
                check=models.Q(label=Trim("label")) & ~models.Q(label=""),
                name="rooms_room_label_trimmed",
            ),
            models.CheckConstraint(
                check=models.Q(beds__gte=1, beds__lte=5),
                name="rooms_room_beds_1_5",
            ),
            models.CheckConstraint(
                check=models.Q(view__in=["sea", "mountain", "yard"]),
                name="rooms_room_view_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["beds", "view"], name="rooms_beds_view_idx"),
        ]

    def clean(self):
        super().clean()
        if self.label is not None:
            self.label = self.label.strip()

        errors = {}
        if not self.label:
            errors["label"] = "Room label cannot be blank."
        if self.beds is not None and not 1 <= self.beds <= 5:
            errors["beds"] = "Room beds must be between 1 and 5."
        if self.view and self.view not in self.View.values:
            errors["view"] = "Select a valid room view."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.label is not None:
            self.label = self.label.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.label
