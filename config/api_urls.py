from rest_framework.routers import DefaultRouter

from rooms.views import RoomViewSet


router = DefaultRouter()
router.register("rooms", RoomViewSet, basename="room")

urlpatterns = router.urls
