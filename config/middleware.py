import logging
from uuid import uuid4


logger = logging.getLogger("hotel.api")
HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}


class RequestLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid4().hex
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id

        if request.path_info.startswith("/api/v1/"):
            status_code = response.status_code
            match = request.resolver_match
            method = request.method
            fields = {
                "request_id": request.request_id,
                "route": match.route if match else None,
                "method": method if method in HTTP_METHODS else "OTHER",
                "status_code": status_code,
            }
            if status_code >= 400:
                data = getattr(response, "data", None)
                error = data.get("error") if isinstance(data, dict) else None
                if isinstance(error, dict) and "code" in error:
                    fields["error_code"] = error["code"]
                elif status_code == 500:
                    fields["error_code"] = "internal_server_error"
                elif status_code == 404 and match is None:
                    fields["error_code"] = "not_found"
                level = logging.ERROR if status_code >= 500 else logging.WARNING
                logger.log(level, "api.error", extra=fields)
            else:
                logger.info("api.request", extra=fields)

        return response
