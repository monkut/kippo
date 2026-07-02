import datetime
from collections.abc import Sequence
from http import HTTPStatus
from typing import Any

from accounts.models import KippoUser
from django.conf import settings
from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from .definitions import SkipReason
from .exceptions import ProjectStartDateRequiredError
from .models import (
    KippoProject,
    KippoProjectBillingEntry,
    KippoProjectContract,
    KippoProjectOrganizationCategory,
    KippoProjectStatus,
    KippoProjectUserStatisfactionResult,
    ProjectAssignmentRate,
    ProjectMonthlyAssignment,
    ProjectMonthlyCost,
    ProjectWeeklyEffort,
    ProjectWeeklyEffortUnlock,
)
from .permissions import IsSuperuserOrOwnOrgReadUpdateCreate, IsSuperuserOrReadUpdateCreateOwn
from .serializers import (
    KippoProjectBillingEntrySerializer,
    KippoProjectContractSerializer,
    KippoProjectOrganizationCategorySerializer,
    KippoProjectSerializer,
    KippoProjectUserStatisfactionResultSerializer,
    OrganizationMemberSerializer,
    ProjectAssignmentPatternSerializer,
    ProjectAssignmentRateSerializer,
    ProjectMonthlyAssignmentSerializer,
    ProjectMonthlyCostSerializer,
    ProjectWeeklyEffortSerializer,
    ProjectWeeklyEffortUnlockSerializer,
)
from .services.autoassign import auto_create_future_assignments
from .services.forecast import ProjectAssignmentForecastManager
from .services.suggest import ProjectAssignmentSuggestionManager


class KippoProjectOrganizationCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only list of selectable project categories (kippo#43).

    Backs the kippo-ui project create/edit form category picker.

    **Organization Scoping:**
    - Regular users see global default categories plus the categories of organizations they belong to.
    - Superusers see all active categories.

    **Filtering:**
    - organization: UUID filter — narrows to that organization's categories (intersected with the
      user's memberships) while still including the global defaults.

    **Permissions:**
    - Read (GET): Authenticated users (organization-scoped for regular users).
    """

    serializer_class = KippoProjectOrganizationCategorySerializer
    permission_classes = [IsAuthenticated]
    queryset = KippoProjectOrganizationCategory.objects.filter(is_active=True).order_by("sort_order", "label")

    @extend_schema(
        parameters=[
            OpenApiParameter(name="organization", description="Filter by organization UUID (globals always included)", required=False, type=str),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Globals + the user's organization categories; superusers see all active categories."""
        queryset = super().get_queryset()
        user = self.request.user
        organization = self.request.query_params.get("organization", None)

        if user.is_superuser:
            if organization:
                queryset = queryset.filter(Q(organization__isnull=True) | Q(organization=organization))
            return queryset

        user_organizations = list(user.organizationmembership_set.values_list("organization", flat=True))
        if organization and organization in {str(org_id) for org_id in user_organizations}:
            return queryset.filter(Q(organization__isnull=True) | Q(organization=organization))
        return queryset.filter(Q(organization__isnull=True) | Q(organization__in=user_organizations))


class KippoProjectViewSet(viewsets.ModelViewSet):
    """
    ViewSet for KippoProject model.

    **Organization Scoping:**
    - Regular users can only access projects from organizations they belong to
    - If a user belongs to multiple organizations, they can access projects from ALL of them
    - Superusers can access projects from all organizations

    **Filtering:**
    - is_active: Filter by display_as_active field (true/false)
    - category: Include only the given category key (exact match)
    - exclude_category: Exclude the given category key (exact match), e.g. drop non-project rows

    **Permissions:**
    - Read (GET): Authenticated users (organization-scoped for regular users)
    - Create (POST): Authenticated users for orgs they belong to; superusers any org
    - Update (PUT/PATCH): Authenticated users for projects in orgs they belong to; superusers any project
    - Delete (DELETE): Superusers only
    """

    serializer_class = KippoProjectSerializer
    permission_classes = [IsSuperuserOrOwnOrgReadUpdateCreate]
    queryset = (
        KippoProject.objects.all()
        # contract (OneToOne) is select_related; its billing_entries back the list's billing_types /
        # contract_amount / total_revenue derived fields (kippo#39 / T14) — fetched to avoid N+1.
        .select_related("organization", "project_manager", "customer", "category", "contract")
        .prefetch_related(
            "github_repositories",
            "contract__billing_entries",
            # assignment_rates backs the serializer's get_assignment_rates (per-role rates).
            "assignment_rates",
            # newest-first statuses feed get_latest_comment without a per-row .latest() query;
            # created_by is select_related for the comment author display name.
            Prefetch(
                "kippoprojectstatus_set",
                queryset=KippoProjectStatus.objects.select_related("created_by").order_by("-created_datetime"),
                to_attr="_prefetched_latest_statuses",
            ),
        )
        .order_by("-created_datetime")
    )

    def perform_create(self, serializer: KippoProjectSerializer) -> None:
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer: KippoProjectSerializer) -> None:
        serializer.save(updated_by=self.request.user)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="is_active",
                description="Filter by active status (display_as_active field)",
                required=False,
                type=bool,
            ),
            OpenApiParameter(
                name="category",
                description="Filter by category (exact match on the KippoProject.category value, e.g. 'PAO')",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="exclude_category",
                description="Exclude projects whose category key matches this value (e.g. 'non-project').",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="search",
                description="Case-insensitive substring match on the project name (for name-search pickers).",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="customer",
                description="Filter by customer UUID (exact match on KippoProject.customer).",
                required=False,
                type=str,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        # Precompute per-page batch data (effort aggregate, survey completions, org holidays) once
        # and expose it through the serializer context so the per-project derived fields do not each
        # re-query. Mirrors ModelViewSet.list() but with the batch-context hook.
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            self._list_batch_context = self._build_batch_context(page)
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        objects = list(queryset)
        self._list_batch_context = self._build_batch_context(objects)
        serializer = self.get_serializer(objects, many=True)
        return Response(serializer.data)

    def get_serializer_context(self) -> dict:
        context = super().get_serializer_context()
        batch = getattr(self, "_list_batch_context", None)
        if batch:
            context.update(batch)
        return context

    def _build_batch_context(self, projects: Sequence[KippoProject]) -> dict:
        """Build the per-page effort / survey / holiday lookups shared via serializer context.

        Returns keys `project_effort_totals`, `project_user_efforts`, `project_survey_completed_users`
        and `public_holidays_by_country`. A project absent from `project_effort_totals` has no logged
        effort (matches KippoProject.get_total_effort() returning None).
        """
        from accounts.models import PublicHoliday

        project_ids = [project.id for project in projects]
        if not project_ids:
            return {}

        # One grouped ProjectWeeklyEffort aggregate for the whole page.
        project_user_efforts: dict = {}
        project_effort_totals: dict = {}
        effort_rows = (
            ProjectWeeklyEffort.objects.filter(project_id__in=project_ids)
            .values("project_id", "user__id", "user__username", "user__first_name", "user__last_name")
            .annotate(user_hours=Sum("hours"))
        )
        for row in effort_rows:
            project_id = row["project_id"]
            project_user_efforts.setdefault(project_id, []).append(
                {
                    "user__id": row["user__id"],
                    "user__username": row["user__username"],
                    "user__first_name": row["user__first_name"],
                    "user__last_name": row["user__last_name"],
                    "user_hours": row["user_hours"],
                }
            )
            project_effort_totals[project_id] = (project_effort_totals.get(project_id) or 0) + (row["user_hours"] or 0)

        # Survey completions per project.
        project_survey_completed_users: dict = {}
        for project_id, created_by_id in KippoProjectUserStatisfactionResult.objects.filter(project_id__in=project_ids).values_list(
            "project_id", "created_by_id"
        ):
            project_survey_completed_users.setdefault(project_id, set()).add(created_by_id)

        # Org public holidays fetched once per distinct country.
        country_ids = {project.organization.default_holiday_country_id for project in projects if project.organization.default_holiday_country_id}
        public_holidays_by_country: dict = {}
        if country_ids:
            for country_id, day in PublicHoliday.objects.filter(country_id__in=country_ids).values_list("country_id", "day"):
                public_holidays_by_country.setdefault(country_id, set()).add(day)

        return {
            "project_effort_totals": project_effort_totals,
            "project_user_efforts": project_user_efforts,
            "project_survey_completed_users": project_survey_completed_users,
            "public_holidays_by_country": public_holidays_by_country,
        }

    def get_queryset(self):
        """Filter queryset based on query parameters and user's organization membership.

        Superusers can access all projects. Regular users can only access projects
        in organizations they belong to.
        """
        queryset = super().get_queryset()

        # Annotate has_requirements via an Exists subquery instead of a per-row .exists() in the serializer.
        from requirements.models import ProjectProblemDefinition

        queryset = queryset.annotate(has_requirements_annotated=Exists(ProjectProblemDefinition.objects.filter(project=OuterRef("pk"))))

        # Filter by user's organization memberships (skip for superusers)
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(organization__in=user_organizations)

        # Filter by is_active parameter
        # When is_active=true, return only projects that are:
        # - display_as_active=True AND is_closed=False
        # When is_active=false, return only projects with display_as_active=False
        is_active = self.request.query_params.get("is_active", None)
        if is_active is not None:
            is_active_bool = is_active.lower() in ["true", "1", "yes"]
            queryset = queryset.filter(display_as_active=is_active_bool)
            if is_active_bool:
                queryset = queryset.filter(is_closed=False)

        # Filter by category (exact match on the KippoProject.category key)
        category = self.request.query_params.get("category", None)
        if category is not None:
            queryset = queryset.filter(category__key=category)

        # Exclude by category (exact match) — lets clients drop e.g. non-project rows server-side.
        exclude_category = self.request.query_params.get("exclude_category", None)
        if exclude_category is not None:
            queryset = queryset.exclude(category__key=exclude_category)

        # Case-insensitive name substring search (powers name-search pickers, e.g. parent_project).
        search = self.request.query_params.get("search", None)
        if search:
            queryset = queryset.filter(name__icontains=search)

        # Filter by customer (exact match on the customer UUID) — efficient FK lookup; lets the
        # parent_project picker scope candidates to the project's customer server-side.
        customer = self.request.query_params.get("customer", None)
        if customer:
            queryset = queryset.filter(customer=customer)

        return queryset

    @extend_schema(
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=inline_serializer(
                    name="ProjectForecastResponse",
                    fields={
                        "estimated_completion_date": serializers.DateField(
                            allow_null=True,
                            help_text="Day-precision completion date; null when the project has no future assignments to project from.",
                        ),
                        "delta_from_target_date_days": serializers.IntegerField(
                            allow_null=True,
                            help_text="Days between estimated_completion_date and target_date. Positive = behind target; negative = ahead.",
                        ),
                        "target_date": serializers.DateField(
                            allow_null=True,
                            help_text="Echo of project.target_date for client-side delta computation.",
                        ),
                    },
                ),
                description="Forecast payload — estimated completion date plus delta vs target_date.",
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(description="project.start_date is required to compute the forecast."),
        },
        description=(
            "Estimated completion date for the project, derived from logged effort + future "
            "ProjectMonthlyAssignment rows. Returns 400 if the project has no start_date set."
        ),
    )
    @action(detail=True, methods=["get"], url_path="forecast")
    def forecast(self, request: Request, pk: str | None = None) -> Response:  # noqa: ARG002
        project = self.get_object()
        try:
            result = ProjectAssignmentForecastManager(project).compute()
        except ProjectStartDateRequiredError as exc:
            return Response(
                {"detail": str(exc), "code": "project_start_date_required"},
                status=HTTPStatus.BAD_REQUEST,
            )
        return Response(result.model_dump(mode="json"))

    @extend_schema(
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=inline_serializer(
                    name="ProjectMembersResponse",
                    fields={"members": OrganizationMemberSerializer(many=True)},
                ),
                description="Members of the project's organization eligible for assignment.",
            ),
        },
        description=(
            "Active members of the project's organization. Lists every KippoUser with an "
            "OrganizationMembership in the project's org, filtered by KippoUser.is_active=True. "
            "Used by kippo-ui's add-assignment picker so day-zero projects can pick from anyone "
            "in the org rather than only users already on the project."
        ),
    )
    @action(detail=True, methods=["get"], url_path="members", permission_classes=[IsAuthenticated])
    def members(self, request: Request, pk: str | None = None) -> Response:  # noqa: ARG002
        project = self.get_object()
        members_qs = (
            KippoUser.objects.filter(
                is_active=True,
                organizationmembership__organization=project.organization,
            )
            .select_related()
            .prefetch_related("organizationmembership_set")
            .order_by("username")
            .distinct()
        )
        memberships_by_user = {m.user_id: m for m in project.organization.organizationmembership_set.filter(user__in=members_qs)}
        payload = [
            {
                "user_id": user.id,
                "username": user.username,
                "display_name": user.display_name.strip() or user.username,
                "github_login": user.github_login or "",
                "is_developer": memberships_by_user[user.id].is_developer if user.id in memberships_by_user else False,
                "is_project_manager": (memberships_by_user[user.id].is_project_manager if user.id in memberships_by_user else False),
            }
            for user in members_qs
        ]
        # Wrap in a named-key response so drf-spectacular doesn't auto-paginate the schema
        # (it does that for `OrganizationMemberSerializer(many=True)` on a ModelViewSet).
        # The picker only ever displays ~10s of users; pagination would be wasted complexity.
        return Response({"members": OrganizationMemberSerializer(payload, many=True).data})

    @extend_schema(
        request=inline_serializer(
            name="SuggestAssignmentsRequest",
            fields={
                "from_month": serializers.DateField(
                    required=False,
                    allow_null=True,
                    help_text=(
                        "First-of-month ISO date to start projecting from. When omitted, defaults "
                        "to the first day of the month after the current date."
                    ),
                ),
            },
        ),
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=inline_serializer(
                    name="SuggestAssignmentsResponse",
                    fields={
                        "patterns": ProjectAssignmentPatternSerializer(many=True),
                    },
                ),
                description=(
                    "0–3 candidate patterns, ranked by closeness to project.target_date "
                    "(feasible patterns first). Greenfield projects skip the P1-max-reuse pattern."
                ),
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(description="project.start_date is required to compute suggestions."),
        },
        description=(
            "Generate up to 3 candidate assignment patterns for the project. Patterns vary "
            "along a continuity gradient (max past-member reuse / blend / most-available pool). "
            "Returns 400 if the project has no start_date set. See monkut/kippo#224 B1-B13."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="suggest-assignments",
        permission_classes=[IsAuthenticated],
        # JSON only — drop the multipart/form-urlencoded variants from the OpenAPI schema.
        parser_classes=[JSONParser],
    )
    def suggest_assignments(self, request: Request, pk: str | None = None) -> Response:  # noqa: ARG002
        project = self.get_object()
        from_month_str = (request.data or {}).get("from_month")
        from_month = None
        if from_month_str:
            try:
                from_month = datetime.date.fromisoformat(from_month_str).replace(day=1)
            except ValueError:
                return Response(
                    {"detail": f"invalid from_month: {from_month_str!r}", "code": "invalid_from_month"},
                    status=HTTPStatus.BAD_REQUEST,
                )
        try:
            patterns = ProjectAssignmentSuggestionManager(project, from_month=from_month).compute()
        except ProjectStartDateRequiredError as exc:
            return Response(
                {"detail": str(exc), "code": "project_start_date_required"},
                status=HTTPStatus.BAD_REQUEST,
            )
        return Response({"patterns": [p.model_dump(mode="json") for p in patterns]})


class ProjectWeeklyEffortViewSet(viewsets.ModelViewSet):
    """
    ViewSet for ProjectWeeklyEffort model.

    **Organization Scoping:**
    - Regular users can only access weekly effort for projects in organizations they belong to
    - If a user belongs to multiple organizations, they can access efforts from ALL of them
    - Superusers can access weekly effort entries from all organizations

    **Filtering:**
    - project: Filter by project UUID
    - user: Filter by user ID
    - user_username: Filter by user's username (must match the logged-in user's username)
    - week_start_gte: Filter by week_start greater than or equal to date (YYYY-MM-DD)
    - week_start_lte: Filter by week_start less than or equal to date (YYYY-MM-DD)

    **Permissions:**
    - Read (GET): Authenticated users (organization-scoped for regular users)
    - Create (POST): Authenticated users (user is auto-set to current user)
    - Update (PUT/PATCH): Authenticated users (own entries only)
    - Delete (DELETE): Superusers only
    """

    serializer_class = ProjectWeeklyEffortSerializer
    permission_classes = [IsSuperuserOrReadUpdateCreateOwn]
    queryset = ProjectWeeklyEffort.objects.all().select_related("project__organization", "user").order_by("-week_start")

    def perform_create(self, serializer: ProjectWeeklyEffortSerializer) -> None:
        """Auto-set the user to the current authenticated user if not provided."""
        if serializer.validated_data.get("user") is None:
            serializer.save(user=self.request.user)
        else:
            serializer.save()

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="user",
                description="Filter by user ID",
                required=False,
                type=int,
            ),
            OpenApiParameter(
                name="user_username",
                description="Filter by user's username (must match logged-in user's username for non-superusers)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="week_start_gte",
                description="Filter by week_start >= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="week_start_lte",
                description="Filter by week_start <= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Filter queryset based on query parameters and user's organization membership.

        Superusers can access all weekly effort entries. Regular users can only access
        entries for projects in organizations they belong to.
        """
        queryset = super().get_queryset()

        # Filter by user's organization memberships through project (skip for superusers)
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(project__organization__in=user_organizations)

        # Filter by project parameter
        project_id = self.request.query_params.get("project", None)
        if project_id:
            queryset = queryset.filter(project__id=project_id)

        # Filter by user parameter
        user_id = self.request.query_params.get("user", None)
        if user_id:
            queryset = queryset.filter(user__id=user_id)

        # Filter by user_username parameter
        # Non-superusers can only filter by their own username
        user_username = self.request.query_params.get("user_username", None)
        if user_username:
            if not user.is_superuser and user_username != user.username:
                # Non-superusers can only query their own data
                queryset = queryset.none()
            else:
                queryset = queryset.filter(user__username=user_username)

        # Filter by week_start_gte parameter
        week_start_gte = self.request.query_params.get("week_start_gte", None)
        if week_start_gte:
            queryset = queryset.filter(week_start__gte=week_start_gte)

        # Filter by week_start_lte parameter
        week_start_lte = self.request.query_params.get("week_start_lte", None)
        if week_start_lte:
            queryset = queryset.filter(week_start__lte=week_start_lte)

        return queryset


class ProjectWeeklyEffortUnlockViewSet(viewsets.ModelViewSet):
    """週間稼働アンロックの申請・承認 API (kippo#33 / T18).

    - **Create (POST)**: 認証ユーザが自分の締め後の週のアンロックを `organization` + `week_start` + `reason` で申請する
      (承認待ち。`user` は申請者本人に固定)。
    - **approve (POST, detail)**: 組織admin (superuser または当該組織の `is_project_manager`) が承認し、
      再ロック期限 `expires_datetime` を設定する。承認されるとその期限まで当該週が編集可能になり、期限経過で自動再ロック。
    - 自分のアンロック申請は自分で承認できない (staff が自分の週を勝手に開けられない原則を踏襲。superuser は除く)。

    アンロックは申請ログでもあるため更新・削除は不可 (GET/POST のみ)。組織スコープ: 非superuserは所属組織の申請のみ閲覧可。
    """

    serializer_class = ProjectWeeklyEffortUnlockSerializer
    permission_classes = [IsAuthenticated]
    queryset = ProjectWeeklyEffortUnlock.objects.all().select_related("organization", "user", "approved_by").order_by("-week_start")
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if not user.is_superuser:
            org_ids = list(user.organizationmembership_set.values_list("organization_id", flat=True))
            queryset = queryset.filter(organization_id__in=org_ids)
        organization_id = self.request.query_params.get("organization", None)
        if organization_id:
            queryset = queryset.filter(organization_id=organization_id)
        return queryset

    def perform_create(self, serializer: ProjectWeeklyEffortUnlockSerializer) -> None:
        """申請者を現在のユーザに固定する (承認待ち状態で作成)。"""
        serializer.save(user=self.request.user, created_by=self.request.user, updated_by=self.request.user)

    @staticmethod
    def _is_org_admin(user: KippoUser, organization: Any) -> bool:  # noqa: ANN401
        if user.is_superuser:
            return True
        return user.organizationmembership_set.filter(organization=organization, is_project_manager=True).exists()

    @extend_schema(
        request=inline_serializer(
            name="ProjectWeeklyEffortUnlockApproveRequest",
            fields={"expires_datetime": serializers.DateTimeField(required=False, help_text="再ロック期限 (省略時はデフォルト7日後)")},
        ),
        responses={200: ProjectWeeklyEffortUnlockSerializer},
        description="アンロック申請を承認し、再ロック期限を設定する (組織admin限定)。",
    )
    @action(detail=True, methods=["post"])
    def approve(self, request: Request, pk: int | None = None) -> Response:
        """組織adminによる承認。"""
        unlock = self.get_object()
        if not self._is_org_admin(request.user, unlock.organization):
            return Response({"detail": "組織adminのみアンロックを承認できます。"}, status=HTTPStatus.FORBIDDEN)
        if unlock.created_by_id == request.user.pk and not request.user.is_superuser:
            return Response({"detail": "自分のアンロック申請は承認できません。"}, status=HTTPStatus.FORBIDDEN)
        parsed_expires = None
        raw_expires = request.data.get("expires_datetime")
        if raw_expires:
            parsed_expires = parse_datetime(raw_expires)
            if parsed_expires is None:
                return Response({"expires_datetime": "ISO8601形式の日時を指定してください。"}, status=HTTPStatus.BAD_REQUEST)
            # a tz-naive datetime would later break aware/naive comparisons in is_active(); assume JST per convention
            if timezone.is_naive(parsed_expires):
                parsed_expires = parsed_expires.replace(tzinfo=settings.JST)
            # a past relock deadline would approve an already-expired (inactive) unlock — reject it
            if parsed_expires <= timezone.now():
                return Response({"expires_datetime": "再ロック期限は未来の日時を指定してください。"}, status=HTTPStatus.BAD_REQUEST)
        unlock.approve(approved_by=request.user, expires_datetime=parsed_expires)
        return Response(self.get_serializer(unlock).data, status=HTTPStatus.OK)


class ProjectAssignmentRateViewSet(viewsets.ModelViewSet):
    """
    ViewSet for ProjectAssignmentRate model.

    Manages daily rates per role for projects.

    **Organization Scoping:**
    - Regular users can only access assignment rates for projects in organizations they belong to
    - Superusers can access assignment rates from all organizations

    **Filtering:**
    - project: Filter by project UUID

    **Permissions:**
    - All CRUD operations require authentication
    - Users can only manage rates for projects in their organizations
    """

    serializer_class = ProjectAssignmentRateSerializer
    permission_classes = [IsAuthenticated]
    queryset = ProjectAssignmentRate.objects.all().select_related("project").order_by("project", "role")

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Filter queryset based on query parameters and user's organization membership.

        Superusers can access all assignment rates. Regular users can only access
        rates for projects in organizations they belong to.
        """
        queryset = super().get_queryset()

        # Filter by user's organization memberships through project (skip for superusers)
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(project__organization__in=user_organizations)

        # Filter by project parameter
        project_id = self.request.query_params.get("project", None)
        if project_id:
            queryset = queryset.filter(project__id=project_id)

        return queryset


def _user_accessible_projects(user: KippoUser) -> QuerySet:
    """Projects visible to ``user`` — all for a superuser, else those in the user's organizations."""
    projects = KippoProject.objects.all()
    if not user.is_superuser and hasattr(user, "organizationmembership_set"):
        user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
        projects = projects.filter(organization__in=user_organizations)
    return projects


class KippoProjectContractViewSet(viewsets.ModelViewSet):
    """Contract (kippo#31) for a project, nested under ``/projects/{project_pk}/contract/``.

    OneToOne — a project has at most one contract. ``GET`` / ``POST`` use the collection URL
    (the list returns the single contract; POST creates it, rejecting a second). ``PUT`` / ``PATCH``
    / ``DELETE`` address the contract by its id at ``/projects/{project_pk}/contract/{id}/`` (the id
    comes from the GET response). Org-scoped: a user sees only contracts for projects in their
    organizations; superusers see all. ``project`` is taken from the URL, not the payload.
    """

    serializer_class = KippoProjectContractSerializer
    permission_classes = [IsAuthenticated]
    queryset = KippoProjectContract.objects.all().select_related("project__organization").order_by("project")

    def get_queryset(self):
        queryset = super().get_queryset().filter(project__in=_user_accessible_projects(self.request.user))
        project_pk = self.kwargs.get("project_pk")
        if project_pk:
            queryset = queryset.filter(project_id=project_pk)
        return queryset

    def perform_create(self, serializer: KippoProjectContractSerializer) -> None:
        project = get_object_or_404(_user_accessible_projects(self.request.user), pk=self.kwargs.get("project_pk"))
        if KippoProjectContract.objects.filter(project=project).exists():
            raise ValidationError("This project already has a contract; edit it via PUT/PATCH.")
        serializer.save(project=project, created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer: KippoProjectContractSerializer) -> None:
        serializer.save(updated_by=self.request.user)


class KippoProjectBillingEntryViewSet(viewsets.ModelViewSet):
    """A project contract's billing-ledger entries (kippo#31), nested under
    ``/projects/{project_pk}/billing-entries/``.

    Org-scoped via the contract's project. Read + write. ``contract`` is resolved from the URL's
    project (the project must already have a contract). ``received_by`` is stamped from the acting
    user when ``is_received`` is set (mirrors the admin); ``received_datetime`` is auto-managed by
    the model.
    """

    serializer_class = KippoProjectBillingEntrySerializer
    permission_classes = [IsAuthenticated]
    queryset = KippoProjectBillingEntry.objects.all().select_related("contract__project__organization", "received_by").order_by("billing_date")

    def get_queryset(self):
        queryset = super().get_queryset().filter(contract__project__in=_user_accessible_projects(self.request.user))
        project_pk = self.kwargs.get("project_pk")
        if project_pk:
            queryset = queryset.filter(contract__project_id=project_pk)
        return queryset

    def _contract_for_request(self) -> KippoProjectContract:
        project = get_object_or_404(_user_accessible_projects(self.request.user), pk=self.kwargs.get("project_pk"))
        contract = getattr(project, "contract", None)
        if contract is None:
            raise ValidationError("This project has no contract; create the contract before adding billing entries.")
        return contract

    def perform_create(self, serializer: KippoProjectBillingEntrySerializer) -> None:
        contract = self._contract_for_request()
        # contract is read_only (set from the URL), so DRF can't build the (contract, billing_date)
        # UniqueConstraint validator — check it here so a duplicate is a clean 400, not a 500.
        billing_date = serializer.validated_data.get("billing_date")
        if contract.billing_entries.filter(billing_date=billing_date).exists():
            raise ValidationError({"billing_date": "A billing entry already exists for this contract and date."})
        received_by = self.request.user if serializer.validated_data.get("is_received") else None
        serializer.save(contract=contract, received_by=received_by, created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer: KippoProjectBillingEntrySerializer) -> None:
        # Mirror the admin: stamp the acting user as receiver when newly marked received (the model
        # save() clears received_by when is_received is unset).
        is_received = serializer.validated_data.get("is_received", serializer.instance.is_received)
        received_by = self.request.user if is_received and not serializer.instance.received_by else serializer.instance.received_by
        serializer.save(received_by=received_by, updated_by=self.request.user)


class ProjectMonthlyAssignmentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for ProjectMonthlyAssignment model.

    Manages monthly workload percentage assignments for users on projects.

    **Organization Scoping:**
    - Regular users can only access assignments for projects in organizations they belong to
    - Superusers can access assignments from all organizations

    **Filtering:**
    - project: Filter by project UUID
    - user: Filter by user ID
    - month: Filter by exact month (YYYY-MM-DD format, day should be 01). Defaults to the
      current month (JST) when none of month/month_gte/month_lte is supplied.
    - month_gte: Filter by month >= date (YYYY-MM-DD format)
    - month_lte: Filter by month <= date (YYYY-MM-DD format)

    **Validation:**
    - User must be a member of the project's organization
    - Month defaults to the project's start_date month if not provided
    - A warning is logged if total assignment percentage for a user exceeds 100% in an organization

    **Permissions:**
    - All CRUD operations require authentication
    - Users can only manage assignments for projects in their organizations
    """

    serializer_class = ProjectMonthlyAssignmentSerializer
    permission_classes = [IsAuthenticated]
    queryset = (
        ProjectMonthlyAssignment.objects.all()
        .select_related("project", "user")
        # memberships back the serializer's slack_username / slack_image_url fields — prefetched to
        # avoid a per-row OrganizationMembership query (resolved from cache in the serializer).
        .prefetch_related("user__organizationmembership_set")
        .order_by("project", "user", "-month")
    )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="user",
                description="Filter by user ID",
                required=False,
                type=int,
            ),
            OpenApiParameter(
                name="month",
                description=(
                    "Filter by exact month (YYYY-MM-DD format). When omitted (and no month_gte/month_lte), defaults to the current month in JST."
                ),
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="month_gte",
                description="Filter by month >= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="month_lte",
                description="Filter by month <= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Filter queryset based on query parameters and user's organization membership.

        Superusers can access all monthly assignments. Regular users can only access
        assignments for projects in organizations they belong to.
        """
        queryset = super().get_queryset()

        # Filter by user's organization memberships through project (skip for superusers)
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(project__organization__in=user_organizations)

        # Filter by project parameter
        project_id = self.request.query_params.get("project", None)
        if project_id:
            queryset = queryset.filter(project__id=project_id)

        # Filter by user parameter
        user_id = self.request.query_params.get("user", None)
        if user_id:
            queryset = queryset.filter(user__id=user_id)

        month = self.request.query_params.get("month", None)
        month_gte = self.request.query_params.get("month_gte", None)
        month_lte = self.request.query_params.get("month_lte", None)

        # On the list view with no month filter at all → default to the current month (JST).
        # Detail actions (retrieve/update/destroy) must reach a row in any month by pk, so the
        # default is list-only. Explicit range filters (month_gte/month_lte) are left as-is.
        # `timezone.localdate()` resolves in settings.TIME_ZONE ("Asia/Tokyo").
        if self.action == "list" and not any((month, month_gte, month_lte)):
            month = timezone.localdate().replace(day=1)

        # Filter by month parameter (exact match)
        if month:
            queryset = queryset.filter(month=month)
        # Filter by month_gte parameter
        if month_gte:
            queryset = queryset.filter(month__gte=month_gte)
        # Filter by month_lte parameter
        if month_lte:
            queryset = queryset.filter(month__lte=month_lte)

        return queryset

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer(
                name="AutoExtendResponse",
                fields={
                    "created": ProjectMonthlyAssignmentSerializer(many=True),
                    "skip_reason": serializers.CharField(allow_null=True),
                },
            ),
        },
    )
    def auto_extend(self, request: Request, project_id: str) -> Response:
        """Manually trigger auto-create of future-month assignments for `project_id` (kippo#19).

        Body: empty.
        Response 200: ``{"created": [...rows...], "skip_reason": null | "<enum>"}``.

        Permission: organization-scoped — non-members get 403 (matches list/retrieve scoping).
        """
        try:
            project = KippoProject.objects.select_related("organization").get(pk=project_id)
        except KippoProject.DoesNotExist:
            return Response({"detail": "Project not found"}, status=HTTPStatus.NOT_FOUND)

        user = request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_org_ids = set(user.organizationmembership_set.values_list("organization_id", flat=True))
            if project.organization_id not in user_org_ids:
                return Response({"detail": "Forbidden"}, status=HTTPStatus.FORBIDDEN)

        triggered_by = user if user.is_authenticated else None
        created_rows, skip_reason = auto_create_future_assignments(project, triggered_by)
        serializer = ProjectMonthlyAssignmentSerializer(created_rows, many=True)
        return Response(
            {
                "created": serializer.data,
                "skip_reason": skip_reason.value if isinstance(skip_reason, SkipReason) else None,
            },
            status=HTTPStatus.OK,
        )


class ProjectMonthlyCostViewSet(viewsets.ModelViewSet):
    """
    ViewSet for ProjectMonthlyCost model.

    Manages monthly cost entries for projects.

    **Organization Scoping:**
    - Regular users can only access costs for projects in organizations they belong to
    - Superusers can access costs from all organizations

    **Filtering:**
    - project: Filter by project UUID
    - service: Filter by service type
    - month: Filter by exact month (YYYY-MM-DD format, day should be 01)
    - month_gte: Filter by month >= date (YYYY-MM-DD format)
    - month_lte: Filter by month <= date (YYYY-MM-DD format)

    **Permissions:**
    - All CRUD operations require authentication
    - Users can only manage costs for projects in their organizations
    """

    serializer_class = ProjectMonthlyCostSerializer
    permission_classes = [IsAuthenticated]
    queryset = ProjectMonthlyCost.objects.all().select_related("project").order_by("project", "-month")

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="service",
                description="Filter by service type",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="month",
                description="Filter by exact month (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="month_gte",
                description="Filter by month >= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="month_lte",
                description="Filter by month <= date (YYYY-MM-DD format)",
                required=False,
                type=str,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Filter queryset based on query parameters and user's organization membership.

        Superusers can access all monthly costs. Regular users can only access
        costs for projects in organizations they belong to.
        """
        queryset = super().get_queryset()

        # Filter by user's organization memberships through project (skip for superusers)
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(project__organization__in=user_organizations)

        # Filter by project parameter
        project_id = self.request.query_params.get("project", None)
        if project_id:
            queryset = queryset.filter(project__id=project_id)

        # Filter by service parameter
        service = self.request.query_params.get("service", None)
        if service:
            queryset = queryset.filter(service=service)

        # Filter by month parameter (exact match)
        month = self.request.query_params.get("month", None)
        if month:
            queryset = queryset.filter(month=month)

        # Filter by month_gte parameter
        month_gte = self.request.query_params.get("month_gte", None)
        if month_gte:
            queryset = queryset.filter(month__gte=month_gte)

        # Filter by month_lte parameter
        month_lte = self.request.query_params.get("month_lte", None)
        if month_lte:
            queryset = queryset.filter(month__lte=month_lte)

        return queryset


class KippoProjectUserStatisfactionResultViewSet(viewsets.ModelViewSet):
    """
    ViewSet for KippoProjectUserStatisfactionResult model (振り返り従業員アンケート).

    Manages project closure retrospective survey results.

    **Organization Scoping:**
    - Regular users can only access surveys for projects in organizations they belong to
    - Superusers can access surveys from all organizations

    **Filtering:**
    - project: Filter by project UUID
    - user: Filter by user ID (created_by)

    **Permissions:**
    - Read (GET): Authenticated users (organization-scoped for regular users)
    - Create (POST): Authenticated users (user is auto-set to current user)
    - Update (PUT/PATCH): Authenticated users (own entries only)
    - Delete (DELETE): Superusers only
    """

    serializer_class = KippoProjectUserStatisfactionResultSerializer
    permission_classes = [IsSuperuserOrReadUpdateCreateOwn]
    queryset = KippoProjectUserStatisfactionResult.objects.all().select_related("project", "created_by").order_by("-created_datetime")

    def perform_create(self, serializer: KippoProjectUserStatisfactionResultSerializer) -> None:
        """Auto-set the created_by to the current authenticated user on create."""
        serializer.save(created_by=self.request.user)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="user",
                description="Filter by user ID (created_by)",
                required=False,
                type=int,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Filter queryset based on query parameters and user's organization membership.

        Superusers can access all surveys. Regular users can only access
        surveys for projects in organizations they belong to.
        """
        queryset = super().get_queryset()

        # Filter by user's organization memberships through project (skip for superusers)
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(project__organization__in=user_organizations)

        # Filter by project parameter
        project_id = self.request.query_params.get("project", None)
        if project_id:
            queryset = queryset.filter(project__id=project_id)

        # Filter by user parameter (created_by)
        user_id = self.request.query_params.get("user", None)
        if user_id:
            queryset = queryset.filter(created_by__id=user_id)

        return queryset
