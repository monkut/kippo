"""ViewSets for Accounts API."""

from http import HTTPStatus
from typing import Any

from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from .models import KippoOrganization, OrganizationMembership, PersonalHoliday, PublicHoliday
from .serializers import (
    OrganizationMemberDetailSerializer,
    OrganizationSerializer,
    PersonalHolidaySerializer,
    PublicHolidaySerializer,
)


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


_TRUTHY = ("true", "1", "yes")


class OrganizationViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """
    Read-only ViewSet for KippoOrganization.

    Exposes the organizations the requester is a member of (superusers see all),
    plus a `members` action that lists active members of a single organization.
    See kippo#14.

    **Endpoints:**
    - `GET /api/organizations/` — list orgs the user belongs to
    - `GET /api/organizations/<id>/members/` — list members of one org

    **Permissions:**
    - Read only: Authenticated users
    - Non-members of `<id>` receive 403 from the `members` action
    """

    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]
    queryset = KippoOrganization.objects.all().order_by("name")

    def get_queryset(self):
        """Scope to the requester's organization memberships (superusers see all)."""
        queryset = super().get_queryset()
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(id__in=user_organizations)
        return queryset

    @extend_schema(
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=inline_serializer(
                    name="OrganizationListResponse",
                    fields={"organizations": OrganizationSerializer(many=True)},
                ),
                description="Organizations the requesting user belongs to. Empty list when the user has no memberships.",
            ),
        },
        description=(
            "List the organizations the requesting user belongs to. Used by kippo-ui to "
            "render an org-wide user picker for views that are not scoped to a single project."
        ),
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401, ARG002
        # Wrap in a named-key response so drf-spectacular doesn't auto-paginate the schema.
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response({"organizations": serializer.data})

    @extend_schema(
        parameters=[
            OpenApiParameter(name="is_developer", description="Filter to developers only.", required=False, type=bool),
            OpenApiParameter(name="is_project_manager", description="Filter to project managers only.", required=False, type=bool),
            OpenApiParameter(
                name="include_inactive",
                description="Include KippoUser.is_active=False users. Default false.",
                required=False,
                type=bool,
            ),
        ],
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=inline_serializer(
                    name="OrganizationMembersResponse",
                    fields={"members": OrganizationMemberDetailSerializer(many=True)},
                ),
                description="Members of the organization, with per-org Slack and email fields.",
            ),
            HTTPStatus.FORBIDDEN: OpenApiResponse(description="Requester is not a member of the organization."),
            HTTPStatus.NOT_FOUND: OpenApiResponse(description="Organization does not exist."),
        },
        description=(
            "Active members of the given organization. Excludes the unassigned bot user "
            "and (by default) KippoUser.is_active=False users. Returns the richer per-org "
            "shape (adds email and slack_* fields) that the per-project endpoint omits."
        ),
    )
    @action(detail=True, methods=["get"], url_path="members", permission_classes=[IsAuthenticated])
    def members(self, request: Request, pk: str | None = None) -> Response:  # noqa: ARG002
        # Explicit 404 vs 403 split: 404 if the org does not exist; 403 if the requester
        # is not a member (instead of the default 404 you'd get from get_queryset filtering).
        try:
            organization = KippoOrganization.objects.get(pk=pk)
        except KippoOrganization.DoesNotExist:
            return Response({"detail": "Not found."}, status=HTTPStatus.NOT_FOUND)

        user = request.user
        if not user.is_superuser:
            user_org_ids = set(user.organizationmembership_set.values_list("organization_id", flat=True))
            if organization.id not in user_org_ids:
                return Response(
                    {"detail": "You are not a member of this organization."},
                    status=HTTPStatus.FORBIDDEN,
                )

        # Use self.request.query_params (Any-typed) over the more strictly-typed request param
        # to match the dynamic-typing pattern already used in projects/viewsets.py for query params.
        include_inactive_raw = self.request.query_params.get("include_inactive")
        include_inactive = include_inactive_raw is not None and include_inactive_raw.lower() in _TRUTHY

        membership_filters: dict[str, Any] = {"organization": organization}
        for key in ("is_developer", "is_project_manager"):
            raw = self.request.query_params.get(key)
            if raw is not None:
                membership_filters[key] = raw.lower() in _TRUTHY

        memberships = (
            OrganizationMembership.objects.filter(**membership_filters)
            .select_related("user")
            .exclude(user__username__startswith=settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX)
            .order_by("user__username")
        )
        if not include_inactive:
            memberships = memberships.filter(user__is_active=True)

        payload = [
            {
                "user_id": m.user.id,
                "username": m.user.username,
                "display_name": m.user.display_name.strip() or m.user.username,
                "first_name": m.user.first_name or "",
                "last_name": m.user.last_name or "",
                "email": m.email or "",
                "github_login": m.user.github_login or "",
                "is_developer": m.is_developer,
                "is_project_manager": m.is_project_manager,
                "slack_username": m.slack_username or "",
                "slack_user_id": m.slack_user_id or "",
                "slack_image_url": m.slack_image_url or "",
            }
            for m in memberships
        ]
        # Wrap in a named-key response so drf-spectacular doesn't auto-paginate the schema.
        return Response({"members": OrganizationMemberDetailSerializer(payload, many=True).data})


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
