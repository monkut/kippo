"""ViewSets for the octocat REST API (kippo#284)."""

from http import HTTPStatus
from typing import Any

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from projects.models import KippoProject
from rest_framework import mixins, serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from .models import GithubRepository
from .serializers import GithubRepositorySerializer


class _ErrorDetailSerializer(serializers.Serializer):
    """Standard DRF-style error envelope: ``{"detail": "<message>"}``."""

    detail = serializers.CharField()


def _user_org_ids(user: Any) -> set:  # noqa: ANN401
    if not (user and user.is_authenticated):
        return set()
    if not hasattr(user, "organizationmembership_set"):
        return set()
    return set(user.organizationmembership_set.values_list("organization", flat=True))


@extend_schema(
    responses={
        HTTPStatus.UNAUTHORIZED: OpenApiResponse(response=_ErrorDetailSerializer, description="Authentication required."),
    }
)
class GithubRepositoryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Read-only org-scoped listing of GithubRepository.

    Write operations live under the nested project route — see
    :class:`ProjectGithubRepositoryViewSet`.
    """

    serializer_class = GithubRepositorySerializer
    permission_classes = [IsAuthenticated]
    queryset = GithubRepository.objects.all().select_related("organization", "project").order_by("name")

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if not user.is_superuser:
            queryset = queryset.filter(organization__in=_user_org_ids(user))
        return queryset

    @extend_schema(
        responses={
            HTTPStatus.OK: GithubRepositorySerializer,
            HTTPStatus.NOT_FOUND: OpenApiResponse(
                response=_ErrorDetailSerializer,
                description="Repository not found (also returned when the repository is in an org the requester does not belong to).",
            ),
        }
    )
    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().retrieve(request, *args, **kwargs)


_PROJECT_FORBIDDEN_RESPONSE = OpenApiResponse(
    response=_ErrorDetailSerializer,
    description="Project is not in any of the requester's organizations.",
)
_PROJECT_NOT_FOUND_RESPONSE = OpenApiResponse(
    response=_ErrorDetailSerializer,
    description="No KippoProject with the given project_id.",
)
_UNAUTHORIZED_RESPONSE = OpenApiResponse(
    response=_ErrorDetailSerializer,
    description="Authentication required.",
)


@extend_schema(
    responses={
        HTTPStatus.UNAUTHORIZED: _UNAUTHORIZED_RESPONSE,
        HTTPStatus.FORBIDDEN: _PROJECT_FORBIDDEN_RESPONSE,
        HTTPStatus.NOT_FOUND: _PROJECT_NOT_FOUND_RESPONSE,
    }
)
class ProjectGithubRepositoryViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Manage GithubRepository links for a single KippoProject.

    Mounted under ``/api/projects/{project_id}/github-repositories/``.

    - ``POST`` upserts by ``(name, html_url, api_url)`` and sets ``project_id``.
      Returns 201 if the row was created, 200 if it already existed.
    - ``DELETE`` unlinks (``project = NULL``) but keeps the row.
    """

    serializer_class = GithubRepositorySerializer
    permission_classes = [IsAuthenticated]
    queryset = GithubRepository.objects.all().select_related("organization", "project")

    def _get_project_or_403(self) -> KippoProject:
        project_id = self.kwargs["project_id"]
        project = get_object_or_404(KippoProject, pk=project_id)
        user = self.request.user
        if not user.is_superuser and project.organization_id not in _user_org_ids(user):
            # Mirror DRF's PermissionDenied -> 403 without leaking existence.
            self.permission_denied(self.request, message="Project is not in any of your organizations.")
        return project

    def get_queryset(self):
        project = self._get_project_or_403()
        return super().get_queryset().filter(project=project).order_by("name")

    @extend_schema(
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=GithubRepositorySerializer,
                description="Repository already existed and was linked (idempotent).",
            ),
            HTTPStatus.CREATED: OpenApiResponse(
                response=GithubRepositorySerializer,
                description="New repository row created and linked.",
            ),
            HTTPStatus.FORBIDDEN: _PROJECT_FORBIDDEN_RESPONSE,
            HTTPStatus.NOT_FOUND: _PROJECT_NOT_FOUND_RESPONSE,
            HTTPStatus.CONFLICT: OpenApiResponse(
                response=_ErrorDetailSerializer,
                description=(
                    "A GithubRepository with the given (name, html_url, api_url) already exists in a different "
                    "organization and cannot be reparented via the API. No mutation performed."
                ),
            ),
        }
    )
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        project = self._get_project_or_403()

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        name = data["name"]
        html_url = data["html_url"]
        api_url = data["api_url"]

        existing = GithubRepository.objects.filter(name=name, html_url=html_url, api_url=api_url).first()

        if existing is not None:
            if existing.organization_id != project.organization_id:
                return Response(
                    {
                        "detail": (
                            "A GithubRepository with this (name, html_url, api_url) already exists in a different "
                            "organization and cannot be reparented via the API."
                        )
                    },
                    status=HTTPStatus.CONFLICT,
                )
            if existing.project_id != project.pk:
                existing.project = project
                existing.updated_by = request.user
                existing.save(update_fields=["project", "updated_by", "updated_datetime"])
            return Response(self.get_serializer(existing).data, status=HTTPStatus.OK)

        # Create path. NOTE: GithubRepository.save() triggers a GitHub labels API call
        # on first insert when settings.OCTOCAT_APPLY_DEFAULT_LABELSET is True
        # (see octocat/models.py:66-79) — same behavior as admin-create today.
        repo = GithubRepository(
            organization=project.organization,
            project=project,
            name=name,
            html_url=html_url,
            api_url=api_url,
            label_set=data.get("label_set"),
            created_by=request.user,
            updated_by=request.user,
        )
        repo.save()
        return Response(self.get_serializer(repo).data, status=HTTPStatus.CREATED)

    @extend_schema(
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(description="Repository unlinked from project; row retained."),
            HTTPStatus.FORBIDDEN: _PROJECT_FORBIDDEN_RESPONSE,
            HTTPStatus.NOT_FOUND: OpenApiResponse(
                response=_ErrorDetailSerializer,
                description="No such repository under this project (also when the repository exists but is linked to a different project).",
            ),
        }
    )
    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        # Unlink (set project = NULL) but keep the row.
        instance = self.get_object()
        instance.project = None
        instance.updated_by = request.user
        instance.save(update_fields=["project", "updated_by", "updated_datetime"])
        return Response(status=HTTPStatus.NO_CONTENT)
