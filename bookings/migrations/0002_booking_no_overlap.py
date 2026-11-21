from django.db import migrations


def add_overlap_constraint(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        schema_editor.execute(
            """
            ALTER TABLE bookings_booking
            ADD CONSTRAINT bookings_no_overlap
            EXCLUDE USING gist (
                room_id WITH =,
                tstzrange(check_in, check_out, '[)') WITH &&
            )
            """
        )


def remove_overlap_constraint(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "ALTER TABLE bookings_booking DROP CONSTRAINT bookings_no_overlap"
        )


class Migration(migrations.Migration):
    dependencies = [("bookings", "0001_initial")]

    operations = [
        migrations.RunPython(add_overlap_constraint, remove_overlap_constraint),
    ]
