"""ViewSets for Accounts API."""

from typing import Any

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from .models import PersonalHoliday, PublicHoliday
from .serializers import PersonalHolidaySerializer, PublicHolidaySerializer


class PersonalHolidayViewSet(viewsets.ModelViewSet):
    """
    ViewSet for PersonalHoliday model (個人休日).

    Users can only manage their own personal holidays.

    **Filtering:**
    - day_gte: Filter by day greater than or equal to date (YYYY-MM-DD)
    - day_lte: Filter by day less than or equal to date (YYYY-MM-DD)

    **Permissions:**
    - All operations: Authenticated users (own holidays only)
    - User is auto-set to current user on create
    """

    serializer_class = PersonalHolidaySerializer
    permission_classes = [IsAuthenticated]
    queryset = PersonalHoliday.objects.all().select_related("user").order_by("-day")

    def perform_create(self, serializer: PersonalHolidaySerializer) -> None:
        """Auto-set the user to the current authenticated user."""
        serializer.save(user=self.request.user)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="day_gte",
                description="Filter by day >= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="day_lte",
                description="Filter by day <= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Filter queryset to only show current user's holidays."""
        queryset = super().get_queryset()

        # Users can only see their own holidays
        user = self.request.user
        queryset = queryset.filter(user=user)

        # Filter by day_gte parameter
        day_gte = self.request.query_params.get("day_gte", None)
        if day_gte:
            queryset = queryset.filter(day__gte=day_gte)

        # Filter by day_lte parameter
        day_lte = self.request.query_params.get("day_lte", None)
        if day_lte:
            queryset = queryset.filter(day__lte=day_lte)

        return queryset


class PublicHolidayViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """
    ViewSet for PublicHoliday model (祝日).

    Read-only access to public holidays for the authenticated user's holiday_country.

    **Filtering:**
    - day_gte: Filter by day greater than or equal to date (YYYY-MM-DD)
    - day_lte: Filter by day less than or equal to date (YYYY-MM-DD)

    **Permissions:**
    - Read only: Authenticated users
    - Returns holidays for user's holiday_country only
    """

    serializer_class = PublicHolidaySerializer
    permission_classes = [IsAuthenticated]
    queryset = PublicHoliday.objects.all().select_related("country").order_by("-day")

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="day_gte",
                description="Filter by day >= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="day_lte",
                description="Filter by day <= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Filter queryset to user's holiday_country."""
        queryset = super().get_queryset()

        # Filter by user's holiday_country
        user = self.request.user
        if hasattr(user, "holiday_country") and user.holiday_country:
            queryset = queryset.filter(country=user.holiday_country)
        else:
            # No holiday_country set, return empty queryset
            queryset = queryset.none()

        # Filter by day_gte parameter
        day_gte = self.request.query_params.get("day_gte", None)
        if day_gte:
            queryset = queryset.filter(day__gte=day_gte)

        # Filter by day_lte parameter
        day_lte = self.request.query_params.get("day_lte", None)
        if day_lte:
            queryset = queryset.filter(day__lte=day_lte)

        return queryset
