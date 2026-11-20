from rest_framework import serializers

from config.api_exceptions import APIConflict
from rooms.models import Room


class StrictModelSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            allowed_fields = {field.field_name for field in self._writable_fields}
            unknown_fields = set(data.keys()) - allowed_fields
            if unknown_fields:
                raise serializers.ValidationError(
                    {field: ["Unknown field."] for field in sorted(unknown_fields)}
                )
        return super().to_internal_value(data)


class RoomSerializer(StrictModelSerializer):
    label = serializers.CharField(max_length=100, trim_whitespace=True)
    beds = serializers.IntegerField(min_value=1, max_value=5)
    view = serializers.ChoiceField(choices=Room.View.choices)

    class Meta:
        model = Room
        fields = ["id", "label", "beds", "view"]
        read_only_fields = ["id"]

    def validate_label(self, value):
        normalized_label = value.strip()
        duplicate_rooms = Room.objects.filter(label__iexact=normalized_label)
        if self.instance is not None:
            duplicate_rooms = duplicate_rooms.exclude(pk=self.instance.pk)
        if duplicate_rooms.exists():
            raise APIConflict(
                "duplicate_room_label", "A room with this label already exists."
            )
        return normalized_label


class RoomFilterSerializer(serializers.Serializer):
    view = serializers.ChoiceField(choices=Room.View.choices, required=False)
    beds = serializers.IntegerField(min_value=1, max_value=5, required=False)
