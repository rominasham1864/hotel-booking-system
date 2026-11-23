from django.urls import path
from drf_spectacular.views import SpectacularAPIView
from rest_framework.routers import DefaultRouter

from bookings.views import BookingCreateAPIView
from rooms.views import RoomViewSet


router = DefaultRouter()
router.register("rooms", RoomViewSet, basename="room")

urlpatterns = [
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("bookings/", BookingCreateAPIView.as_view(), name="booking-create"),
    *router.urls,
]
