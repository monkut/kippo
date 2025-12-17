from typing import Any

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from .models import KippoProject, ProjectAssignmentRate, ProjectWeeklyEffort
from .permissions import IsSuperuserOrReadUpdateOnly
from .serializers import KippoProjectSerializer, ProjectAssignmentRateSerializer, ProjectWeeklyEffortSerializer


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
    queryset = KippoProject.objects.all().select_related("organization", "project_manager").order_by("-created_datetime")

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
        is_active = self.request.query_params.get("is_active", None)
        if is_active is not None:
            is_active_bool = is_active.lower() in ["true", "1", "yes"]
            queryset = queryset.filter(display_as_active=is_active_bool)

        return queryset


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
    - week_start_gte: Filter by week_start greater than or equal to date (YYYY-MM-DD)
    - week_start_lte: Filter by week_start less than or equal to date (YYYY-MM-DD)

    **Permissions:**
    - Read (GET): Authenticated users (organization-scoped for regular users)
    - Update (PUT/PATCH): Authenticated users (organization-scoped for regular users)
    - Create (POST): Superusers only
    - Delete (DELETE): Superusers only
    """

    serializer_class = ProjectWeeklyEffortSerializer
    permission_classes = [IsSuperuserOrReadUpdateOnly]
    queryset = ProjectWeeklyEffort.objects.all().select_related("project", "user").order_by("-week_start")

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
