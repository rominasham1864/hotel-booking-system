from django.db import IntegrityError, transaction
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from bookings.models import Booking
from config.api_exceptions import APIConflict
from rooms.models import Room
from rooms.pagination import RoomPagination
from rooms.serializers import RoomFilterSerializer, RoomSerializer


class RoomViewSet(viewsets.ModelViewSet):
    serializer_class = RoomSerializer
    pagination_class = RoomPagination
    queryset = Room.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != "list":
            return queryset

        filters = RoomFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        if "view" in filters.validated_data:
            queryset = queryset.filter(view=filters.validated_data["view"])
        if "beds" in filters.validated_data:
            queryset = queryset.filter(beds=filters.validated_data["beds"])
        return queryset

    @staticmethod
    def _constraint_name(exc):
        cause = exc.__cause__
        diagnostic = getattr(cause, "diag", None)
        return getattr(diagnostic, "constraint_name", None)

    def _save_serializer(self, serializer, **save_kwargs):
        try:
            with transaction.atomic():
                serializer.save(**save_kwargs)
        except IntegrityError as exc:
            if self._constraint_name(exc) == "rooms_room_label_ci_uniq":
                raise APIConflict(
                    "duplicate_room_label", "A room with this label already exists."
                ) from exc
            raise

    def perform_create(self, serializer):
        self._save_serializer(serializer)

    def perform_update(self, serializer):
        self._save_serializer(serializer)

    def destroy(self, request, *args, **kwargs):
        confirmation = request.query_params.get("confirm")
        if confirmation not in (None, "true", "false"):
            raise ValidationError({"confirm": ["Use 'true' or 'false'."]})

        with transaction.atomic():
            room = get_object_or_404(
                Room.objects.select_for_update(), pk=self.kwargs[self.lookup_field]
            )
            self.check_object_permissions(request, room)
            bookings = Booking.objects.filter(room=room)

            if bookings.exists() and confirmation != "true":
                raise APIConflict(
                    "confirmation_required",
                    "Deleting this room will permanently delete its associated bookings. "
                    "Repeat with confirm=true to continue.",
                )

            bookings.delete()
            room.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
