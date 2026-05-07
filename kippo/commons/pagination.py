"""Project-wide DRF pagination classes."""

from rest_framework.pagination import PageNumberPagination


class CustomPageNumberPagination(PageNumberPagination):
    """PageNumberPagination with a client-overridable page_size capped at max_page_size.

    Default page_size matches the historical REST_FRAMEWORK['PAGE_SIZE'] (50) so existing
    clients keep their current behavior. Clients can request a larger page via
    ``?page_size=<N>``; values exceeding ``max_page_size`` are silently clamped by DRF.
    """

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200
