"""ViewSets for Accounts API."""

import datetime
from collections import defaultdict
from http import HTTPStatus
from typing import Any

from commons.viewsets import OrganizationFilterMixin, organization_ids_for_user
from dateutil.relativedelta import relativedelta
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


def _calc_available_workdays_in_month(
    memberships: list[OrganizationMembership],
    month_start: datetime.date,
) -> dict[Any, int]:
    """Return {user_id: available_workdays_count} for the calendar month starting at `month_start`.

    Available = committed weekdays in the month, minus public holidays for the
    user's holiday_country, minus the user's personal holidays. Batched to avoid
    N+1 queries.
    """
    month_end = month_start + relativedelta(months=1) - datetime.timedelta(days=1)
    user_ids = [m.user_id for m in memberships]

    # Personal holidays touching the month (expand by `duration`).
    personal_by_user: dict[Any, set[datetime.date]] = defaultdict(set)
    earliest_relevant_start = month_start - datetime.timedelta(days=365)
    for ph in PersonalHoliday.objects.filter(user_id__in=user_ids, day__gte=earliest_relevant_start, day__lte=month_end):
        for offset in range(ph.duration):
            d = ph.day + datetime.timedelta(days=offset)
            if month_start <= d <= month_end:
                personal_by_user[ph.user_id].add(d)

    # Public holidays per country for the month window.
    country_ids = {m.user.holiday_country_id for m in memberships if m.user.holiday_country_id}
    public_by_country: dict[Any, set[datetime.date]] = defaultdict(set)
    if country_ids:
        for ph in PublicHoliday.objects.filter(country_id__in=country_ids, day__gte=month_start, day__lte=month_end):
            public_by_country[ph.country_id].add(ph.day)

    result: dict[Any, int] = {}
    for membership in memberships:
        committed = set(membership.committed_weekdays)
        personal = personal_by_user.get(membership.user_id, set())
        public = public_by_country.get(membership.user.holiday_country_id, set())
        count = 0
        current = month_start
        while current <= month_end:
            if current.weekday() in committed and current not in personal and current not in public:
                count += 1
            current += datetime.timedelta(days=1)
        result[membership.user_id] = count
    return result


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


class OrganizationViewSet(OrganizationFilterMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
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
        # The model *is* the organization, so scope on its own primary key.
        return self.filter_by_organization(queryset, "id")

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
            OpenApiParameter(
                name="month",
                description=(
                    "When set (YYYY-MM-DD; any day, snapped to month start), each member's "
                    "`available_work_days` field is populated with the count of available "
                    "workdays in that month (committed weekdays minus public/personal holidays). "
                    "Omitted or invalid → `available_work_days` is null."
                ),
                required=False,
                type=str,
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
            HTTPStatus.BAD_REQUEST: OpenApiResponse(description="`month` query parameter is not in YYYY-MM-DD format."),
            HTTPStatus.FORBIDDEN: OpenApiResponse(description="Requester is not a member of the organization."),
            HTTPStatus.NOT_FOUND: OpenApiResponse(description="Organization does not exist."),
        },
        description=(
            "Active members of the given organization. Excludes the unassigned bot user "
            "and (by default) KippoUser.is_active=False users. Returns the richer per-org "
            "shape (adds email and slack_* fields) that the per-project endpoint omits. "
            "Pass `?month=YYYY-MM-DD` to include each member's `available_work_days` for "
            "that calendar month — used by the assignments-matrix UI to express row totals "
            "in person-days instead of percentages."
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
        if not user.is_superuser and organization.id not in organization_ids_for_user(user):
            return Response(
                {"detail": "You are not a member of this organization."},
                status=HTTPStatus.FORBIDDEN,
            )

        # Use self.request.query_params (Any-typed) over the more strictly-typed request param
        # to match the dynamic-typing pattern already used in projects/viewsets.py for query params.
        include_inactive_raw = self.request.query_params.get("include_inactive")
        include_inactive = include_inactive_raw is not None and include_inactive_raw.lower() in _TRUTHY

        month_raw = self.request.query_params.get("month")
        month_start: datetime.date | None = None
        if month_raw:
            try:
                month_start = datetime.datetime.strptime(month_raw, "%Y-%m-%d").date().replace(day=1)  # noqa: DTZ007
            except ValueError:
                return Response(
                    {"detail": "month must be YYYY-MM-DD"},
                    status=HTTPStatus.BAD_REQUEST,
                )

        membership_filters: dict[str, Any] = {"organization": organization}
        for key in ("is_developer", "is_project_manager"):
            raw = self.request.query_params.get(key)
            if raw is not None:
                membership_filters[key] = raw.lower() in _TRUTHY

        memberships = list(
            OrganizationMembership.objects.filter(**membership_filters)
            .select_related("user")
            .exclude(user__username__startswith=settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX)
            .order_by("user__username")
        )
        if not include_inactive:
            memberships = [m for m in memberships if m.user.is_active]

        workdays_by_user: dict[Any, int] = _calc_available_workdays_in_month(memberships, month_start) if month_start else {}

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
                "available_work_days": workdays_by_user.get(m.user_id) if month_start else None,
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
