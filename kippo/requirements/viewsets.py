import logging
from typing import Any

import requests
from commons.functions import is_uuid
from commons.viewsets import OrganizationFilterMixin, scope_to_user_organizations, user_can_access_organization
from django.db.models import Count, FloatField, Model, OuterRef, Subquery, Sum
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from projects.models import KippoProject
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer
from rest_framework.views import APIView

from .definitions import AssumptionCategories, EvaluationStates
from .functions import (
    EvaluationServiceError,
    NoEstimatesError,
    NoTechnicalRequirementsError,
    evaluate_requirement,
    schedule_technical_requirements,
)
from .models import (
    AssumptionEvaluation,
    BusinessRequirementEvaluation,
    ProblemDefinitionEvaluation,
    ProjectAssumption,
    ProjectBusinessRequirement,
    ProjectBusinessRequirementCategory,
    ProjectBusinessRequirementComment,
    ProjectBusinessRequirementEstimate,
    ProjectProblemDefinition,
    ProjectProblemDefinitionComment,
    ProjectTechnicalRequirement,
    ProjectTechnicalRequirementCategory,
    ProjectTechnicalRequirementComment,
    ProjectTechnicalRequirementGithubIssue,
    TechnicalRequirementEvaluation,
)
from .serializers import (
    AssumptionEvaluationSerializer,
    BusinessRequirementEvaluationSerializer,
    ErrorResponseSerializer,
    ProblemDefinitionEvaluationSerializer,
    ProjectAssumptionSerializer,
    ProjectBusinessRequirementCategorySerializer,
    ProjectBusinessRequirementCommentSerializer,
    ProjectBusinessRequirementDetailSerializer,
    ProjectBusinessRequirementEstimateSerializer,
    ProjectBusinessRequirementListSerializer,
    ProjectBusinessRequirementSerializer,
    ProjectProblemDefinitionCommentSerializer,
    ProjectProblemDefinitionSerializer,
    ProjectTechnicalRequirementCategorySerializer,
    ProjectTechnicalRequirementCommentSerializer,
    ProjectTechnicalRequirementDetailSerializer,
    ProjectTechnicalRequirementGithubIssueSerializer,
    ProjectTechnicalRequirementSerializer,
    ScheduleEstimationRequestSerializer,
    ScheduleEstimationResponseSerializer,
    TechnicalRequirementEvaluationSerializer,
)

logger = logging.getLogger(__name__)

PROJECT_PARAMETER = OpenApiParameter(name="project", description="Filter by project UUID", required=False, type=str)
TOP_LEVEL_ONLY_PARAMETER = OpenApiParameter(
    name="top_level_only",
    description="Return only top-level comments (no replies)",
    required=False,
    type=bool,
)


def evaluate_schema(evaluation_serializer_class: type[BaseSerializer], label: str):  # noqa: ANN201
    """Schema for the `evaluate` action of the viewset whose elements are called `label`."""
    return extend_schema(
        request=None,
        responses={201: evaluation_serializer_class},
        description=f"Trigger evaluation of this {label} via external service.",
    )


def accept_schema(requirement_serializer_class: type[BaseSerializer], label: str):  # noqa: ANN201
    """Schema for the `accept` action of the evaluation viewset whose parent elements are called `label`."""
    return extend_schema(
        request=None,
        responses={200: requirement_serializer_class},
        description=f"Accept this evaluation's suggestions and apply them to the {label}.",
    )


def toggle_resolved_schema(comment_serializer_class: type[BaseSerializer]):  # noqa: ANN201
    return extend_schema(
        description="Toggle the is_resolved status for a top-level comment.",
        responses={200: comment_serializer_class},
    )


class ProjectFilterMixin(OrganizationFilterMixin):
    """Scope a viewset to the user's organizations and the optional `?project=<uuid>` filter."""

    def get_queryset(self):
        queryset = self.filter_by_organization(super().get_queryset(), "project__organization")  # type: ignore[misc]
        project_id = self.request.query_params.get("project")
        if not project_id:
            return queryset
        if not is_uuid(project_id):
            raise ValidationError({"project": "Must be a valid UUID."})
        return queryset.filter(project_id=project_id)


class EvaluateActionMixin:
    """`evaluate` action: send the element to the evaluation service and record the result.

    The viewset sets `evaluation_serializer_class`; its `Meta.model` is the evaluation model.
    """

    evaluation_serializer_class: type[BaseSerializer]

    @action(detail=True, methods=["post"])
    def evaluate(self, request: Request, pk: int | None = None) -> Response:
        element = self.get_object()  # type: ignore[attr-defined]
        try:
            result = evaluate_requirement(element)
        except (EvaluationServiceError, requests.RequestException) as e:
            logger.exception("Evaluation service error for %s %s", element._meta.verbose_name, pk)
            return Response({"error": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        evaluation = self.evaluation_serializer_class.Meta.model.objects.create(  # type: ignore[attr-defined]
            requirement=element,
            evaluation_result=result["evaluation_result"],
            feedback=result["feedback"],
            suggested_title=result.get("suggested_title", ""),
            suggested_details=result.get("suggested_details", ""),
            created_by=request.user,
        )
        element.save_evaluation_state(result["evaluation_result"])
        return Response(self.evaluation_serializer_class(evaluation).data, status=status.HTTP_201_CREATED)


class NestedUnderRequirementMixin(OrganizationFilterMixin):
    """Scope a nested viewset to the parent element named in the URL.

    `parent_lookup_kwarg` is the URL kwarg carrying the parent pk; `parent_field` is the FK to it.
    """

    parent_lookup_kwarg: str
    parent_field = "requirement"

    def parent_id_filter(self) -> dict:
        return {f"{self.parent_field}_id": self.kwargs[self.parent_lookup_kwarg]}  # type: ignore[attr-defined]

    def get_queryset(self):
        queryset = self.filter_by_organization(super().get_queryset(), f"{self.parent_field}__project__organization")  # type: ignore[misc]
        return queryset.filter(**self.parent_id_filter())

    def get_parent(self) -> Model:
        """The parent element named in the URL. 404 when it does not exist or is outside the user's organizations."""
        parent_model = self.queryset.model._meta.get_field(self.parent_field).related_model  # type: ignore[attr-defined]
        accessible_parents = scope_to_user_organizations(parent_model.objects.all(), self.request.user, "project__organization")
        return get_object_or_404(accessible_parents, pk=self.kwargs[self.parent_lookup_kwarg])  # type: ignore[attr-defined]

    def perform_create(self, serializer: BaseSerializer[Any]) -> None:
        serializer.save(**{self.parent_field: self.get_parent()})


class UserStampedNestedMixin(NestedUnderRequirementMixin):
    """Nested viewset whose rows record `created_by` / `updated_by`."""

    def perform_create(self, serializer: BaseSerializer[Any]) -> None:
        serializer.save(**{self.parent_field: self.get_parent()}, created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer: BaseSerializer[Any]) -> None:
        serializer.save(updated_by=self.request.user)


class RequirementCommentViewSet(UserStampedNestedMixin, viewsets.ModelViewSet):
    # No docstring: drf-spectacular reads each concrete viewset's own docstring as the API description.
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        # created_by backs created_by_name; the reverse self-relation backs each comment's replies.
        queryset = queryset.select_related("created_by").prefetch_related(f"{queryset.model._meta.model_name}_set__created_by")
        if self.request.query_params.get("top_level_only") == "true":
            queryset = queryset.filter(parent_comment__isnull=True)
        return queryset


class ToggleResolvedMixin:
    @action(detail=True, methods=["post"])
    def toggle_resolved(self, request: Request, pk: int | None = None, **kwargs) -> Response:
        comment = self.get_object()  # type: ignore[attr-defined]
        if comment.parent_comment is not None:
            return Response(
                {"error": "Only top-level comments can be resolved"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        comment.is_resolved = not comment.is_resolved
        comment.updated_by = request.user
        comment.save()
        return Response(self.get_serializer(comment).data)  # type: ignore[attr-defined]


class RequirementEvaluationViewSet(NestedUnderRequirementMixin, viewsets.ReadOnlyModelViewSet):
    # No docstring: drf-spectacular reads each concrete viewset's own docstring as the API description.
    # The viewset sets `requirement_serializer_class`, the serializer of the evaluated (parent) element.
    permission_classes = [IsAuthenticated]
    requirement_serializer_class: type[BaseSerializer]

    @action(detail=True, methods=["post"])
    def accept(self, request: Request, pk: int | None = None, **kwargs) -> Response:
        evaluation = self.get_object()
        element = evaluation.requirement
        if evaluation.suggested_title:
            element.title = evaluation.suggested_title
        if evaluation.suggested_details:
            element.details = evaluation.suggested_details
        element.save_evaluation_state(EvaluationStates.VALID.value, update_fields=["title", "details"])
        return Response(self.requirement_serializer_class(element).data)


@extend_schema_view(
    list=extend_schema(parameters=[PROJECT_PARAMETER]),
    evaluate=evaluate_schema(ProblemDefinitionEvaluationSerializer, "problem definition"),
)
class ProjectProblemDefinitionViewSet(EvaluateActionMixin, ProjectFilterMixin, viewsets.ModelViewSet):
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
    evaluation_serializer_class = ProblemDefinitionEvaluationSerializer
    permission_classes = [IsAuthenticated]


@extend_schema_view(
    list=extend_schema(
        parameters=[
            PROJECT_PARAMETER,
            OpenApiParameter(
                name="category",
                description="Filter by category (assumption or constraint)",
                required=False,
                type=str,
                enum=["assumption", "constraint"],
            ),
        ]
    ),
    evaluate=evaluate_schema(AssumptionEvaluationSerializer, "assumption"),
)
class ProjectAssumptionViewSet(EvaluateActionMixin, ProjectFilterMixin, viewsets.ModelViewSet):
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
    evaluation_serializer_class = AssumptionEvaluationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        category = self.request.query_params.get("category")
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


@extend_schema_view(list=extend_schema(parameters=[PROJECT_PARAMETER]))
class ProjectBusinessRequirementCategoryViewSet(ProjectFilterMixin, viewsets.ModelViewSet):
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


@extend_schema_view(list=extend_schema(parameters=[PROJECT_PARAMETER]))
class ProjectTechnicalRequirementCategoryViewSet(ProjectFilterMixin, viewsets.ModelViewSet):
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


@extend_schema_view(
    list=extend_schema(
        parameters=[
            PROJECT_PARAMETER,
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
    evaluate=evaluate_schema(BusinessRequirementEvaluationSerializer, "business requirement"),
)
class ProjectBusinessRequirementViewSet(EvaluateActionMixin, ProjectFilterMixin, viewsets.ModelViewSet):
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
    evaluation_serializer_class = BusinessRequirementEvaluationSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return ProjectBusinessRequirementListSerializer
        if self.action == "retrieve":
            return ProjectBusinessRequirementDetailSerializer
        return ProjectBusinessRequirementSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        problem_id = self.request.query_params.get("problem")
        category_id = self.request.query_params.get("category")
        if problem_id:
            queryset = queryset.filter(problems__id=problem_id)
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        queryset = queryset.distinct()

        # The list serializer derives category_name (FK), problems_data (M2M), technical_requirements_count
        # and total_estimate_days per row — resolve them in bulk to avoid per-row queries. The estimate
        # sum uses a correlated Subquery so it does not fan out against the technical-requirements Count.
        if self.action == "list":
            estimate_total = Subquery(
                ProjectBusinessRequirementEstimate.objects.filter(requirement__business_requirements=OuterRef("pk"))
                .values("requirement__business_requirements")
                .annotate(total=Sum("days"))
                .values("total")[:1],
                output_field=FloatField(),
            )
            queryset = (
                queryset.select_related("category", "project")
                .prefetch_related("problems")
                .annotate(
                    technical_requirements_count_annotated=Count("projecttechnicalrequirement", distinct=True),
                    total_estimate_days_annotated=estimate_total,
                )
            )
        return queryset


@extend_schema_view(
    list=extend_schema(
        parameters=[
            PROJECT_PARAMETER,
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
    evaluate=evaluate_schema(TechnicalRequirementEvaluationSerializer, "technical requirement"),
)
class ProjectTechnicalRequirementViewSet(EvaluateActionMixin, ProjectFilterMixin, viewsets.ModelViewSet):
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
    evaluation_serializer_class = TechnicalRequirementEvaluationSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ProjectTechnicalRequirementDetailSerializer
        return ProjectTechnicalRequirementSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        business_requirement_id = self.request.query_params.get("business_requirement")
        category_id = self.request.query_params.get("category")
        if business_requirement_id:
            queryset = queryset.filter(business_requirements__id=business_requirement_id)
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        return queryset.distinct()


@extend_schema_view(
    list=extend_schema(parameters=[TOP_LEVEL_ONLY_PARAMETER]),
    toggle_resolved=toggle_resolved_schema(ProjectProblemDefinitionCommentSerializer),
)
class ProjectProblemDefinitionCommentViewSet(ToggleResolvedMixin, RequirementCommentViewSet):
    """
    ViewSet for ProjectProblemDefinitionComment model.

    Nested under: /problem-definitions/{problem_definition_pk}/comments/

    Comments on problem definitions with nested reply support.

    **Organization Scoping:**
    - Regular users can only access comments for projects in their organizations
    - Superusers can access all comments

    **Filtering:**
    - top_level_only: Return only top-level comments (true/false)

    **Actions:**
    - toggle_resolved: Toggle the is_resolved status for a top-level comment
    """

    queryset = ProjectProblemDefinitionComment.objects.all()
    serializer_class = ProjectProblemDefinitionCommentSerializer
    parent_lookup_kwarg = "problem_definition_pk"


@extend_schema_view(
    list=extend_schema(parameters=[TOP_LEVEL_ONLY_PARAMETER]),
    toggle_resolved=toggle_resolved_schema(ProjectBusinessRequirementCommentSerializer),
)
class ProjectBusinessRequirementCommentViewSet(ToggleResolvedMixin, RequirementCommentViewSet):
    """
    ViewSet for ProjectBusinessRequirementComment model.

    Nested under: /business-requirements/{business_requirement_pk}/comments/

    Comments on business requirements with nested reply support.

    **Organization Scoping:**
    - Regular users can only access comments for projects in their organizations
    - Superusers can access all comments

    **Filtering:**
    - top_level_only: Return only top-level comments (true/false)

    **Actions:**
    - toggle_resolved: Toggle the is_resolved status for a top-level comment
    """

    queryset = ProjectBusinessRequirementComment.objects.all()
    serializer_class = ProjectBusinessRequirementCommentSerializer
    parent_lookup_kwarg = "business_requirement_pk"


@extend_schema_view(list=extend_schema(parameters=[TOP_LEVEL_ONLY_PARAMETER]))
class ProjectTechnicalRequirementCommentViewSet(RequirementCommentViewSet):
    """
    ViewSet for ProjectTechnicalRequirementComment model.

    Nested under: /technical-requirements/{technical_requirement_pk}/comments/

    Comments on technical requirements with nested reply support.

    **Organization Scoping:**
    - Regular users can only access comments for projects in their organizations
    - Superusers can access all comments

    **Filtering:**
    - top_level_only: Return only top-level comments (true/false)
    """

    queryset = ProjectTechnicalRequirementComment.objects.all()
    serializer_class = ProjectTechnicalRequirementCommentSerializer
    parent_lookup_kwarg = "technical_requirement_pk"


class ProjectBusinessRequirementEstimateViewSet(UserStampedNestedMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectBusinessRequirementEstimate model.

    Nested under: /technical-requirements/{technical_requirement_pk}/estimates/

    Estimates for technical requirements (days and confidence level).

    **Organization Scoping:**
    - Regular users can only access estimates for projects in their organizations
    - Superusers can access all estimates
    """

    queryset = ProjectBusinessRequirementEstimate.objects.all()
    serializer_class = ProjectBusinessRequirementEstimateSerializer
    permission_classes = [IsAuthenticated]
    parent_lookup_kwarg = "technical_requirement_pk"


class ProjectTechnicalRequirementGithubIssueViewSet(NestedUnderRequirementMixin, viewsets.ModelViewSet):
    """
    ViewSet for ProjectTechnicalRequirementGithubIssue model.

    Nested under: /technical-requirements/{technical_requirement_pk}/github-issues/

    Links between technical requirements and GitHub issues.

    **Organization Scoping:**
    - Regular users can only access links for projects in their organizations
    - Superusers can access all links
    """

    queryset = ProjectTechnicalRequirementGithubIssue.objects.all()
    serializer_class = ProjectTechnicalRequirementGithubIssueSerializer
    permission_classes = [IsAuthenticated]
    parent_lookup_kwarg = "technical_requirement_pk"
    parent_field = "technical_requirement"


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

        if not user_can_access_organization(request.user, project.organization_id):
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


@extend_schema_view(accept=accept_schema(ProjectProblemDefinitionSerializer, "problem definition"))
class ProblemDefinitionEvaluationViewSet(RequirementEvaluationViewSet):
    """Read-only viewset for problem definition evaluations with accept action."""

    queryset = ProblemDefinitionEvaluation.objects.all()
    serializer_class = ProblemDefinitionEvaluationSerializer
    requirement_serializer_class = ProjectProblemDefinitionSerializer
    parent_lookup_kwarg = "problem_definition_pk"


@extend_schema_view(accept=accept_schema(ProjectAssumptionSerializer, "assumption"))
class AssumptionEvaluationViewSet(RequirementEvaluationViewSet):
    """Read-only viewset for assumption evaluations with accept action."""

    queryset = AssumptionEvaluation.objects.all()
    serializer_class = AssumptionEvaluationSerializer
    requirement_serializer_class = ProjectAssumptionSerializer
    parent_lookup_kwarg = "assumption_pk"


@extend_schema_view(accept=accept_schema(ProjectBusinessRequirementSerializer, "business requirement"))
class BusinessRequirementEvaluationViewSet(RequirementEvaluationViewSet):
    """Read-only viewset for business requirement evaluations with accept action."""

    queryset = BusinessRequirementEvaluation.objects.all()
    serializer_class = BusinessRequirementEvaluationSerializer
    requirement_serializer_class = ProjectBusinessRequirementSerializer
    parent_lookup_kwarg = "business_requirement_pk"


@extend_schema_view(accept=accept_schema(ProjectTechnicalRequirementSerializer, "technical requirement"))
class TechnicalRequirementEvaluationViewSet(RequirementEvaluationViewSet):
    """Read-only viewset for technical requirement evaluations with accept action."""

    queryset = TechnicalRequirementEvaluation.objects.all()
    serializer_class = TechnicalRequirementEvaluationSerializer
    requirement_serializer_class = ProjectTechnicalRequirementSerializer
    parent_lookup_kwarg = "technical_requirement_pk"
