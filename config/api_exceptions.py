from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


class APIConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"

    def __init__(self, error_code, message):
        self.error_code = error_code
        super().__init__(detail=message, code=error_code)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, APIConflict):
        response.data = {
            "error": {
                "code": exc.error_code,
                "message": str(exc.detail),
            }
        }
        return response

    if isinstance(exc, serializers.ValidationError):
        response.data = {
            "error": {
                "code": "validation_error",
                "message": "Request validation failed.",
                "fields": response.data,
            }
        }
        return response

    if response.status_code == status.HTTP_404_NOT_FOUND:
        detail = response.data.get("detail", "The requested room was not found.")
        message = str(detail)
        code = "page_not_found" if "page" in message.lower() else "room_not_found"
        response.data = {
            "error": {
                "code": code,
                "message": message,
            }
        }
        return response

    if response.status_code == status.HTTP_400_BAD_REQUEST:
        detail = response.data.get("detail", "Request validation failed.")
        response.data = {
            "error": {
                "code": "validation_error",
                "message": str(detail),
            }
        }
        return response

    detail = response.data.get("detail", "The request could not be completed.")
    try:
        code = exc.get_codes()
    except AttributeError:
        code = "request_error"
    if not isinstance(code, str):
        code = "request_error"
    response.data = {
        "error": {
            "code": code,
            "message": str(detail),
        }
    }
    return response
