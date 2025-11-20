"""API views for the projects app."""

import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseBadRequest, JsonResponse, request as DjangoRequest  # noqa: N812
from django.views.decorators.http import require_http_methods
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication

from .functions import get_user_session_organization
from .models import KippoProject, ProjectWeeklyEffort

logger = logging.getLogger(__name__)


class SessionAndJWTSpectacularAPIView(SpectacularAPIView):
    """
    OpenAPI schema view that supports both Django session and JWT authentication.

    This allows users logged in via OAuth to access the schema without needing JWT tokens.
    """

    authentication_classes = [SessionAuthentication, JWTAuthentication]


class SessionAndJWTSpectacularSwaggerView(SpectacularSwaggerView):
    """
    Swagger UI view that supports both Django session and JWT authentication.

    This allows users logged in via OAuth to access the API documentation without needing JWT tokens.
    """

    authentication_classes = [SessionAuthentication, JWTAuthentication]


@staff_member_required
@require_http_methods(["GET"])
def project_status_api(request: DjangoRequest, project_id: str) -> JsonResponse:
    """
    API endpoint that provides project status data in JSON format for graphing.

    Restricted to users belonging to the associated organization.

    Returns:
        - ProjectWeeklyEffort data per date/user for the given project_id
          (includes ALL dates even if they precede or exceed the project's start/end dates)
        - Expected effort for each unique ProjectWeeklyEffort date
        - Total allocated effort for the project
        - Project start/end dates

    Args:
        request: Django request object
        project_id: UUID of the KippoProject

    Returns:
        JsonResponse with project status data
    """
    try:
        selected_organization, user_organizations = get_user_session_organization(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e.args))

    # Get the project and verify organization access
    try:
        project = KippoProject.objects.get(id=project_id, organization__in=user_organizations)
    except KippoProject.DoesNotExist:
        return HttpResponseBadRequest(f"Project with id {project_id} not found or access denied")

    # Get all ProjectWeeklyEffort entries for this project
    project_weekly_efforts = ProjectWeeklyEffort.objects.filter(project=project).order_by("week_start", "user__first_name", "user__last_name")

    # Prepare weekly effort data (cumulative per user)
    from collections import defaultdict

    user_cumulative_hours = defaultdict(int)
    weekly_effort_data = []
    unique_dates = set()
    for effort in project_weekly_efforts:
        user_cumulative_hours[effort.user.display_name] += effort.hours
        weekly_effort_data.append(
            {
                "week_start": effort.week_start.isoformat(),
                "user": effort.user.display_name,
                "user_display_name": effort.user.display_name,
                "hours": effort.hours,
                "cumulative_hours": user_cumulative_hours[effort.user.display_name],
            }
        )
        unique_dates.add(effort.week_start)

    # Calculate expected effort for each unique date
    expected_effort_by_date = []
    for date in sorted(unique_dates):
        expected_effort_days, expected_effort_hours = project.get_expected_effort(at_date=date)
        expected_effort_by_date.append(
            {
                "date": date.isoformat(),
                "expected_effort_days": expected_effort_days,
                "expected_effort_hours": expected_effort_hours,
            }
        )

    # Get total allocated effort
    allocated_effort_hours = project.allocated_effort_hours

    # Prepare response data
    response_data = {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "start_date": project.start_date.isoformat() if project.start_date else None,
            "target_date": project.target_date.isoformat() if project.target_date else None,
            "allocated_staff_days": project.allocated_staff_days,
            "allocated_effort_hours": allocated_effort_hours,
        },
        "weekly_effort": weekly_effort_data,
        "expected_effort_by_date": expected_effort_by_date,
    }

    return JsonResponse(response_data)
