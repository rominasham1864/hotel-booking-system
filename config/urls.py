from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.defaults import page_not_found, server_error


def health(request):
    return JsonResponse({"status": "ok"})


def handler404(request, exception):
    if request.path_info.startswith("/api/v1/"):
        return JsonResponse(
            {"error": {"code": "not_found", "message": "API path not found."}},
            status=404,
        )
    return page_not_found(request, exception)


def handler500(request):
    if request.path_info.startswith("/api/v1/"):
        return JsonResponse(
            {
                "error": {
                    "code": "internal_server_error",
                    "message": "An internal server error occurred.",
                }
            },
            status=500,
        )
    return server_error(request)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("config.api_urls")),
    path("health/", health, name="health"),
]
