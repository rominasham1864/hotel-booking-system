from drf_spectacular.utils import OpenApiResponse
from rest_framework import serializers


class APIErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        help_text="Field-level validation messages, when available.",
    )


class APIErrorSerializer(serializers.Serializer):
    error = APIErrorDetailSerializer()


def error_response(description):
    return OpenApiResponse(response=APIErrorSerializer, description=description)
