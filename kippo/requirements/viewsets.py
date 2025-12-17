from typing import Any

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from projects.models import KippoProject
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer
from rest_framework.views import APIView

from .definitions import AssumptionCategories
from .functions import NoEstimatesError, NoTechnicalRequirementsError, schedule_technical_requirements
from .models import (
    ProjectAssumption,
    ProjectBusinessRequirement,
    ProjectBusinessRequirementCategory,
    ProjectBusinessRequirementComment,
    ProjectBusinessRequirementEstimate,
    ProjectProblemDefinition,
    ProjectTechnicalRequirement,
    ProjectTechnicalRequirementCategory,
    ProjectTechnicalRequirementComment,
    ProjectTechnicalRequirementGithubIssue,
)
from .serializers import (
    ErrorResponseSerializer,
    ProjectAssumptionSerializer,
    ProjectBusinessRequirementCategorySerializer,
    ProjectBusinessRequirementCommentSerializer,
    ProjectBusinessRequirementDetailSerializer,
    ProjectBusinessRequirementEstimateSerializer,
    ProjectBusinessRequirementListSerializer,
    ProjectBusinessRequirementSerializer,
    ProjectProblemDefinitionSerializer,
    ProjectTechnicalRequirementCategorySerializer,
    ProjectTechnicalRequirementCommentSerializer,
    ProjectTechnicalRequirementDetailSerializer,
    ProjectTechnicalRequirementGithubIssueSerializer,
    ProjectTechnicalRequirementSerializer,
    ScheduleEstimationRequestSerializer,
    ScheduleEstimationResponseSerializer,
)


class OrganizationFilterMixin:
    """Mixin to filter querysets by user's organization membership."""

    request: Request

    def filter_by_organization(self, queryset: QuerySet, project_path: str = "project") -> QuerySet:
        """Filter queryset by user's organization memberships.

        Args:
            queryset: The queryset to filter
            project_path: The path to the project field (e.g., "project" or "requirement__project")
        """
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            filter_key = f"{project_path}__organization__in"
            queryset = queryset.filter(**{filter_key: user_organizations})
        return queryset


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
        ]
    )
)
class ProjectProblemDefinitionViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectProblemDefinition model.

    Problem definitions describe the issues that a project aims to solve.

    **Organization Scoping:**
    - Regular users can only access problem definitions for projects in their organizations
    - Superusers can access all problem definitions

    **Filtering:**
    - project: Filter by project UUID
    """

    queryset = ProjectProblemDefinition.objects.all()
    serializer_class = ProjectProblemDefinitionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset)
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="category",
                description="Filter by category (assumption or constraint)",
                required=False,
                type=str,
                enum=["assumption", "constraint"],
            ),
        ]
    )
)
class ProjectAssumptionViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectAssumption model.

    Assumptions and constraints (前提条件と制約事項) for a project.

    **Organization Scoping:**
    - Regular users can only access assumptions for projects in their organizations
    - Superusers can access all assumptions

    **Filtering:**
    - project: Filter by project UUID
    - category: Filter by category (assumption or constraint)
    """

    queryset = ProjectAssumption.objects.all()
    serializer_class = ProjectAssumptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset)
        project_id = self.request.query_params.get("project")
        category = self.request.query_params.get("category")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if category:
            queryset = queryset.filter(category=category)
        return queryset

    @extend_schema(
        responses={200: dict},
        description="Return available assumption category choices.",
    )
    @action(detail=False, methods=["get"])
    def categories(self, request: Request) -> Response:
        """Return available assumption category choices."""
        choices = [{"value": choice[0], "label": choice[1]} for choice in AssumptionCategories.choices()]
        return Response(choices)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
        ]
    )
)
class ProjectBusinessRequirementCategoryViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectBusinessRequirementCategory model.

    Categories for organizing business requirements within a project.

    **Organization Scoping:**
    - Regular users can only access categories for projects in their organizations
    - Superusers can access all categories

    **Filtering:**
    - project: Filter by project UUID
    """

    queryset = ProjectBusinessRequirementCategory.objects.all()
    serializer_class = ProjectBusinessRequirementCategorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset)
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
        ]
    )
)
class ProjectTechnicalRequirementCategoryViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectTechnicalRequirementCategory model.

    Categories for organizing technical requirements within a project.

    **Organization Scoping:**
    - Regular users can only access categories for projects in their organizations
    - Superusers can access all categories

    **Filtering:**
    - project: Filter by project UUID
    """

    queryset = ProjectTechnicalRequirementCategory.objects.all()
    serializer_class = ProjectTechnicalRequirementCategorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset)
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="problem",
                description="Filter by problem definition ID",
                required=False,
                type=int,
            ),
            OpenApiParameter(
                name="category",
                description="Filter by category ID",
                required=False,
                type=int,
            ),
        ],
        responses={200: ProjectBusinessRequirementListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        responses={200: ProjectBusinessRequirementDetailSerializer},
    ),
)
class ProjectBusinessRequirementViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectBusinessRequirement model.

    Business requirements define what the project needs to accomplish.

    **Organization Scoping:**
    - Regular users can only access requirements for projects in their organizations
    - Superusers can access all requirements

    **Filtering:**
    - project: Filter by project UUID
    - problem: Filter by problem definition ID
    - category: Filter by category ID

    **Serializers:**
    - List: ProjectBusinessRequirementListSerializer (lightweight)
    - Retrieve: ProjectBusinessRequirementDetailSerializer (includes technical requirements and comments)
    - Create/Update: ProjectBusinessRequirementSerializer
    """

    queryset = ProjectBusinessRequirement.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return ProjectBusinessRequirementListSerializer
        if self.action == "retrieve":
            return ProjectBusinessRequirementDetailSerializer
        return ProjectBusinessRequirementSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset)
        project_id = self.request.query_params.get("project")
        problem_id = self.request.query_params.get("problem")
        category_id = self.request.query_params.get("category")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if problem_id:
            queryset = queryset.filter(problems__id=problem_id)
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        return queryset.distinct()


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="business_requirement",
                description="Filter by business requirement ID",
                required=False,
                type=int,
            ),
            OpenApiParameter(
                name="category",
                description="Filter by category ID",
                required=False,
                type=int,
            ),
        ],
        responses={200: ProjectTechnicalRequirementSerializer(many=True)},
    ),
    retrieve=extend_schema(
        responses={200: ProjectTechnicalRequirementDetailSerializer},
    ),
)
class ProjectTechnicalRequirementViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectTechnicalRequirement model.

    Technical requirements (開発要件) linked to business requirements.

    **Organization Scoping:**
    - Regular users can only access requirements for projects in their organizations
    - Superusers can access all requirements

    **Filtering:**
    - project: Filter by project UUID
    - business_requirement: Filter by business requirement ID
    - category: Filter by category ID

    **Serializers:**
    - List/Create/Update: ProjectTechnicalRequirementSerializer
    - Retrieve: ProjectTechnicalRequirementDetailSerializer (includes comments)
    """

    queryset = ProjectTechnicalRequirement.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ProjectTechnicalRequirementDetailSerializer
        return ProjectTechnicalRequirementSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset)
        project_id = self.request.query_params.get("project")
        business_requirement_id = self.request.query_params.get("business_requirement")
        category_id = self.request.query_params.get("category")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if business_requirement_id:
            queryset = queryset.filter(business_requirements__id=business_requirement_id)
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        return queryset.distinct()


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="requirement",
                description="Filter by business requirement ID",
                required=False,
                type=int,
            ),
            OpenApiParameter(
                name="top_level_only",
                description="Return only top-level comments (no replies)",
                required=False,
                type=bool,
            ),
        ]
    )
)
class ProjectBusinessRequirementCommentViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectBusinessRequirementComment model.

    Comments on business requirements with nested reply support.

    **Organization Scoping:**
    - Regular users can only access comments for projects in their organizations
    - Superusers can access all comments

    **Filtering:**
    - requirement: Filter by business requirement ID
    - top_level_only: Return only top-level comments (true/false)

    **Actions:**
    - toggle_resolved: Toggle the is_resolved status for a top-level comment
    """

    queryset = ProjectBusinessRequirementComment.objects.all()
    serializer_class = ProjectBusinessRequirementCommentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset, project_path="requirement__project")
        requirement_id = self.request.query_params.get("requirement")
        top_level_only = self.request.query_params.get("top_level_only")
        if requirement_id:
            queryset = queryset.filter(requirement_id=requirement_id)
        if top_level_only == "true":
            queryset = queryset.filter(parent_comment__isnull=True)
        return queryset

    def perform_create(self, serializer: BaseSerializer[Any]) -> None:
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer: BaseSerializer[Any]) -> None:
        serializer.save(updated_by=self.request.user)

    @extend_schema(
        description="Toggle the is_resolved status for a top-level comment.",
        responses={200: ProjectBusinessRequirementCommentSerializer},
    )
    @action(detail=True, methods=["post"])
    def toggle_resolved(self, request: Request, pk: int | None = None) -> Response:
        """Toggle the is_resolved status for a top-level comment."""
        comment = self.get_object()
        if comment.parent_comment is not None:
            return Response(
                {"error": "Only top-level comments can be resolved"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        comment.is_resolved = not comment.is_resolved
        comment.updated_by = request.user
        comment.save()
        serializer = self.get_serializer(comment)
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="requirement",
                description="Filter by technical requirement ID",
                required=False,
                type=int,
            ),
            OpenApiParameter(
                name="top_level_only",
                description="Return only top-level comments (no replies)",
                required=False,
                type=bool,
            ),
        ]
    )
)
class ProjectTechnicalRequirementCommentViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectTechnicalRequirementComment model.

    Comments on technical requirements with nested reply support.

    **Organization Scoping:**
    - Regular users can only access comments for projects in their organizations
    - Superusers can access all comments

    **Filtering:**
    - requirement: Filter by technical requirement ID
    - top_level_only: Return only top-level comments (true/false)
    """

    queryset = ProjectTechnicalRequirementComment.objects.all()
    serializer_class = ProjectTechnicalRequirementCommentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset, project_path="requirement__project")
        requirement_id = self.request.query_params.get("requirement")
        top_level_only = self.request.query_params.get("top_level_only")
        if requirement_id:
            queryset = queryset.filter(requirement_id=requirement_id)
        if top_level_only == "true":
            queryset = queryset.filter(parent_comment__isnull=True)
        return queryset

    def perform_create(self, serializer: BaseSerializer[Any]) -> None:
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer: BaseSerializer[Any]) -> None:
        serializer.save(updated_by=self.request.user)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="requirement",
                description="Filter by technical requirement ID",
                required=False,
                type=int,
            ),
            OpenApiParameter(
                name="project",
                description="Filter by project UUID",
                required=False,
                type=str,
            ),
        ]
    )
)
class ProjectBusinessRequirementEstimateViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectBusinessRequirementEstimate model.

    Estimates for technical requirements (days and confidence level).

    **Organization Scoping:**
    - Regular users can only access estimates for projects in their organizations
    - Superusers can access all estimates

    **Filtering:**
    - requirement: Filter by technical requirement ID
    - project: Filter by project UUID
    """

    queryset = ProjectBusinessRequirementEstimate.objects.all()
    serializer_class = ProjectBusinessRequirementEstimateSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset, project_path="requirement__project")
        requirement_id = self.request.query_params.get("requirement")
        project_id = self.request.query_params.get("project")
        if requirement_id:
            queryset = queryset.filter(requirement_id=requirement_id)
        if project_id:
            queryset = queryset.filter(requirement__project_id=project_id)
        return queryset

    def perform_create(self, serializer: BaseSerializer[Any]) -> None:
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer: BaseSerializer[Any]) -> None:
        serializer.save(updated_by=self.request.user)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="technical_requirement",
                description="Filter by technical requirement ID",
                required=False,
                type=int,
            ),
        ]
    )
)
class ProjectTechnicalRequirementGithubIssueViewSet(OrganizationFilterMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectTechnicalRequirementGithubIssue model.

    Links between technical requirements and GitHub issues.

    **Organization Scoping:**
    - Regular users can only access links for projects in their organizations
    - Superusers can access all links

    **Filtering:**
    - technical_requirement: Filter by technical requirement ID
    """

    queryset = ProjectTechnicalRequirementGithubIssue.objects.all()
    serializer_class = ProjectTechnicalRequirementGithubIssueSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_organization(queryset, project_path="technical_requirement__project")
        technical_requirement_id = self.request.query_params.get("technical_requirement")
        if technical_requirement_id:
            queryset = queryset.filter(technical_requirement_id=technical_requirement_id)
        return queryset


class ScheduleEstimationAPIView(APIView):
    """
    API endpoint for scheduling technical requirements and estimating completion dates.

    Accepts a project UUID and number of developers, then uses QluTaskScheduler
    to schedule all technical requirements and calculate an estimated completion date.

    **Request Body:**
    - project: UUID of the project to schedule
    - developer_count: Number of developers available (1-100)
    - start_date: Optional start date for scheduling (defaults to today)

    **Response:**
    - estimated_completion_date: When all work is expected to complete
    - total_estimate_days: Sum of all requirement estimates
    - total_confidence_adjusted_days: Sum with confidence adjustments
    - requirements_count: Number of requirements scheduled
    - scheduled_requirements: Detailed list of each scheduled requirement
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ScheduleEstimationRequestSerializer,
        responses={
            200: ScheduleEstimationResponseSerializer,
            400: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
        description="Schedule technical requirements and calculate estimated completion date.",
    )
    def post(self, request: Request) -> Response:
        """Schedule technical requirements and return estimated completion date."""
        serializer = ScheduleEstimationRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        project_id = serializer.validated_data["project"]
        developer_count = serializer.validated_data["developer_count"]
        start_date = serializer.validated_data.get("start_date")

        # Get project and verify user has access
        try:
            project = KippoProject.objects.get(id=project_id)
        except KippoProject.DoesNotExist:
            return Response({"error": f"Project not found: {project_id}"}, status=status.HTTP_404_NOT_FOUND)

        # Check organization access for non-superusers
        user = request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            if project.organization_id not in user_organizations:
                return Response(
                    {"error": "You do not have access to this project"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            result = schedule_technical_requirements(
                project=project,
                developer_count=developer_count,
                start_date=start_date,
            )
        except (NoTechnicalRequirementsError, NoEstimatesError, ValueError) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        response_serializer = ScheduleEstimationResponseSerializer(data=result)
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.data)
