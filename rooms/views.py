from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from bookings.models import Booking
from config.api_exceptions import APIConflict
from config.api_schema import error_response
from rooms.models import Room
from rooms.pagination import RoomPagination
from rooms.serializers import RoomFilterSerializer, RoomSerializer


ROOM_NOT_FOUND = error_response("room_not_found. The room ID is invalid or does not exist.")
INVALID_ROOM = error_response("validation_error. The request body is malformed or invalid.")
DUPLICATE_LABEL = error_response("duplicate_room_label. A room with this label already exists.")
ROOM_WRITE_DESCRIPTION = (
    "Unknown and read-only fields are rejected. Labels are trimmed and compared "
    "case-insensitively for uniqueness. Views are normalized to lowercase."
)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            RoomFilterSerializer,
            OpenApiParameter(
                "page", {"type": "integer", "minimum": 1}, default=1
            ),
            OpenApiParameter(
                "page_size", {"type": "integer", "minimum": 1, "maximum": 100},
                default=20,
            ),
        ],
        responses={
            200: RoomSerializer(many=True),
            400: error_response("validation_error. Invalid filters or pagination parameters."),
            404: error_response("page_not_found. The requested page does not exist."),
        },
    ),
    create=extend_schema(
        description=ROOM_WRITE_DESCRIPTION,
        responses={201: RoomSerializer, 400: INVALID_ROOM, 409: DUPLICATE_LABEL},
    ),
    retrieve=extend_schema(responses={200: RoomSerializer, 404: ROOM_NOT_FOUND}),
    update=extend_schema(
        description=ROOM_WRITE_DESCRIPTION,
        responses={
            200: RoomSerializer, 400: INVALID_ROOM, 404: ROOM_NOT_FOUND, 409: DUPLICATE_LABEL
        },
    ),
    partial_update=extend_schema(
        description=ROOM_WRITE_DESCRIPTION,
        responses={
            200: RoomSerializer, 400: INVALID_ROOM, 404: ROOM_NOT_FOUND, 409: DUPLICATE_LABEL
        },
    ),
)
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

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "confirm", str, enum=["true", "false"],
                description="Use true to delete the room and all associated bookings.",
            ),
        ],
        responses={
            204: None,
            400: error_response("validation_error. Invalid confirm value."),
            404: ROOM_NOT_FOUND,
            409: error_response("confirmation_required. The room has associated bookings."),
        },
    )
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
