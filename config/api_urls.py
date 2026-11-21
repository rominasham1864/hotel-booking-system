from django.urls import path
from rest_framework.routers import DefaultRouter

from bookings.views import BookingCreateAPIView
from rooms.views import RoomViewSet


router = DefaultRouter()
router.register("rooms", RoomViewSet, basename="room")

urlpatterns = [
    path("bookings/", BookingCreateAPIView.as_view(), name="booking-create"),
    *router.urls,
]
