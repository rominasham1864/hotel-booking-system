from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination


class PaginationQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100)


class RoomPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        query = PaginationQuerySerializer(
            data={
                "page": request.query_params.get("page", 1),
                "page_size": request.query_params.get("page_size", self.page_size),
            }
        )
        query.is_valid(raise_exception=True)
        return super().paginate_queryset(queryset, request, view)
