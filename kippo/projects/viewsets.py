import datetime
from http import HTTPStatus
from typing import Any

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from .exceptions import ProjectStartDateRequiredError
from .models import (
    KippoProject,
    KippoProjectUserStatisfactionResult,
    ProjectAssignmentRate,
    ProjectMonthlyAssignment,
    ProjectMonthlyCost,
    ProjectWeeklyEffort,
)
from .permissions import IsSuperuserOrReadUpdateCreateOwn, IsSuperuserOrReadUpdateOnly
from .serializers import (
    KippoProjectSerializer,
    KippoProjectUserStatisfactionResultSerializer,
    ProjectAssignmentRateSerializer,
    ProjectMonthlyAssignmentSerializer,
    ProjectMonthlyCostSerializer,
    ProjectWeeklyEffortSerializer,
)
from .services.forecast import ProjectAssignmentForecastManager
from .services.suggest import ProjectAssignmentSuggestionManager


class KippoProjectViewSet(viewsets.ModelViewSet):
    """
    ViewSet for KippoProject model.

    **Organization Scoping:**
    - Regular users can only access projects from organizations they belong to
    - If a user belongs to multiple organizations, they can access projects from ALL of them
    - Superusers can access projects from all organizations

    **Filtering:**
    - is_active: Filter by display_as_active field (true/false)

    **Permissions:**
    - Read (GET): Authenticated users (organization-scoped for regular users)
    - Update (PUT/PATCH): Authenticated users (organization-scoped for regular users)
    - Create (POST): Superusers only
    - Delete (DELETE): Superusers only
    """

    serializer_class = KippoProjectSerializer
    permission_classes = [IsSuperuserOrReadUpdateOnly]
    queryset = (
        KippoProject.objects.all()
        .select_related("organization", "project_manager")
        .prefetch_related("github_repositories")
        .order_by("-created_datetime")
    )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="is_active",
                description="Filter by active status (display_as_active field)",
                required=False,
                type=bool,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Filter queryset based on query parameters and user's organization membership.

        Superusers can access all projects. Regular users can only access projects
        in organizations they belong to.
        """
        queryset = super().get_queryset()

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

        return queryset

    @extend_schema(
        responses={
            HTTPStatus.OK: inline_serializer(
                name="ProjectForecastResponse",
                fields={
                    "estimated_completion_date": serializers.DateField(allow_null=True),
                    "delta_from_target_date_days": serializers.IntegerField(allow_null=True),
                    "target_date": serializers.DateField(allow_null=True),
                },
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(description="project.start_date is required to compute the forecast"),
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
        request=inline_serializer(
            name="SuggestAssignmentsRequest",
            fields={"from_month": serializers.DateField(required=False, allow_null=True)},
        ),
        responses={
            HTTPStatus.OK: inline_serializer(
                name="SuggestAssignmentsResponse",
                fields={
                    "patterns": serializers.ListField(child=serializers.JSONField()),
                },
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(description="project.start_date is required to compute suggestions"),
        },
        description=(
            "Generate up to 3 candidate assignment patterns for the project. Patterns vary "
            "along a continuity gradient (max past-member reuse / blend / most-available pool). "
            "Returns 400 if the project has no start_date set. See monkut/kippo#224 B1-B13."
        ),
    )
    @action(detail=True, methods=["post"], url_path="suggest-assignments", permission_classes=[IsAuthenticated])
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
    queryset = ProjectWeeklyEffort.objects.all().select_related("project", "user").order_by("-week_start")

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
    - month: Filter by exact month (YYYY-MM-DD format, day should be 01)
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
    queryset = ProjectMonthlyAssignment.objects.all().select_related("project", "user").order_by("project", "user", "-month")

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
