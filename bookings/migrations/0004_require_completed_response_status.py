from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bookings", "0003_idempotencyrecord_and_more")]

    operations = [
        migrations.RemoveConstraint(
            model_name="idempotencyrecord",
            name="bookings_idempotency_valid_state",
        ),
        migrations.AddConstraint(
            model_name="idempotencyrecord",
            constraint=models.CheckConstraint(
                name="bookings_idempotency_valid_state",
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
                        response_status__isnull=False,
                        response_status=201,
                        response_body__isnull=False,
                    )
                ),
            ),
        ),
    ]
