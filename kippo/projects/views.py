import datetime
import json
import logging
import urllib.parse
from collections import defaultdict
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections import Counter

from commons.functions import ui_url
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    request as DjangoRequest,  # noqa: N812
)
from django.shortcuts import redirect, render
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from tasks.models import KippoTaskStatus

from kippo.awsclients import get_s3_client, s3_key_exists

from .functions import get_user_session_organization
from .models import KippoMilestone, KippoProject, ProjectWeeklyEffort
from .services.progress import effort_meter, expected_effort_hours

logger = logging.getLogger(__name__)


class AssigneeStatus(NamedTuple):
    assignee: str | None
    task_count: int
    available_workdays: int
    estimated_workdays: int
    load_percentage: str


@staff_member_required
def set_user_session_organization(request: DjangoRequest, organization_id: str = None) -> HttpResponse:
    user_organizations = list(request.user.organizations)
    if not organization_id:
        return HttpResponseBadRequest('required "organization_id" not given!')
    if not user_organizations:
        return HttpResponseBadRequest(f"user({request.user.username}) has no OrganizationMemberships!")

    if organization_id not in [str(o.id) for o in user_organizations]:
        logger.debug(f"Invalid organization_id({organization_id}) for user({request.user.username}) using user first")
        organization_id = user_organizations[0].id

    request.session["organization_id"] = str(organization_id)
    return HttpResponseRedirect(f"{settings.URL_PREFIX}/projects/")  # go reload the page with the set org


def _get_milestone_assignee_status(milestone: KippoMilestone) -> list[AssigneeStatus]:
    """Prepare the milestone specific assignee status"""
    assignee_status = []
    # build assignee_status
    # - AssigneeStatus.assignee
    # - AssineeStatus.task_count
    # - AssigneeStatus.available_workdays
    # - AssigneeStatus.estimated_workdays
    # - AssigneeStatus.load_percentage
    assignee_available_workdays: Counter = milestone.get_assignee_workdays()
    assignee_estimated_workdays: Counter = milestone.get_assignee_estimated_workdays()
    assignee_task_counts: Counter = milestone.get_assignee_task_counts()
    for assignee, available_workdays in assignee_available_workdays.items():
        estimated_workdays = assignee_estimated_workdays[assignee]
        percentage_display = "-"
        if available_workdays:
            exceeded_workdays_display = ""
            if estimated_workdays > available_workdays:
                exceeded_workdays = estimated_workdays - available_workdays
                exceeded_workdays_display = f"( + {exceeded_workdays:>3} days )"
            percentage = round((estimated_workdays / available_workdays) * 100, 2)
            percentage_display = f"{percentage:>6} % {exceeded_workdays_display}"
        elif not available_workdays and estimated_workdays:
            exceeded_workdays_display = f"( + {estimated_workdays} days )"
            percentage_display = exceeded_workdays_display
        status = AssigneeStatus(
            assignee=str(assignee),
            task_count=assignee_task_counts[assignee],
            available_workdays=available_workdays,
            estimated_workdays=estimated_workdays,
            load_percentage=percentage_display,
        )
        assignee_status.append(status)
    return assignee_status


@staff_member_required
def view_milestone_status(request: DjangoRequest, milestone_id: str | None = None) -> HttpResponse:
    try:
        selected_organization, user_organizations = get_user_session_organization(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e.args))

    milestones = KippoMilestone.objects.filter(project__organization=selected_organization, is_completed=False, project__is_closed=False).order_by(
        "target_date", "project", "title"
    )
    if milestone_id:
        milestones = milestones.filter(id=milestone_id)
        if not milestones:
            return HttpResponseBadRequest(f"milestone_id does not exist: {milestone_id}")
    if not KippoTaskStatus.objects.filter(task__project__organization=selected_organization):
        milestones = []
        messages.add_message(
            request,
            messages.ERROR,
            "No KippoTaskStatus Items defined For Organization Projects -- Unable to prepare Milestone Data!",
        )
    selected_milestone = None
    assignee_status = []
    if milestone_id:
        selected_milestone = milestones[0]
        assignee_status = _get_milestone_assignee_status(milestone=selected_milestone)

    active_projects = KippoProject.objects.filter(is_closed=False, organization=selected_organization).order_by("name")
    context = {
        "milestones": milestones,
        "milestone": selected_milestone,
        "assignee_status": assignee_status,
        "messages": messages.get_messages(request),
        "active_projects": active_projects,
        "selected_organization": selected_organization,
        "organizations": user_organizations,
    }

    return render(request, "projects/view_milestones_status.html", context)


@staff_member_required
def data_download_waiter(request: DjangoRequest):
    raw_filename = request.GET.get("filename", None)
    back_path = request.GET.get("back_path", f"{settings.URL_PREFIX}/admin/projects/projectweeklyeffort/")
    referer = request.META.get("HTTP_REFERER", None)
    parsed_full_path = urllib.parse.urlparse(request.get_full_path())
    current_path = parsed_full_path.path
    query = parsed_full_path.query

    filename = None
    if raw_filename:
        filename = urllib.parse.unquote(raw_filename)

    referer_path = None
    if referer:
        referer_path = urllib.parse.urlparse(referer).path
    if all((referer, current_path == referer_path, filename, s3_key_exists(settings.DUMPDATA_S3_BUCKETNAME, filename))):
        return redirect(f"{settings.URL_PREFIX}/projects/download/done/?{query}")

    return render(request, "projects/download_waiter.html", {"back_path": back_path})


@staff_member_required
def data_download_done(request: DjangoRequest):
    raw_filename = request.GET.get("filename", None)
    back_path = request.GET.get("back_path", f"{settings.URL_PREFIX}/admin/projects/projectweeklyeffort/")
    referer = request.META.get("HTTP_REFERER", None)
    current_path = urllib.parse.urlparse(request.get_full_path()).path

    filename = None
    if raw_filename:
        filename = urllib.parse.unquote(raw_filename)

    referer_path = None
    if referer:
        referer_path = urllib.parse.urlparse(referer).path
    expired_seconds = request.GET.get("expired_seconds", 3600)

    if all((referer, current_path == referer_path, filename, s3_key_exists(settings.DUMPDATA_S3_BUCKETNAME, filename))):
        presigned_url = get_s3_client().generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": settings.DUMPDATA_S3_BUCKETNAME, "Key": filename},
            ExpiresIn=expired_seconds,
            HttpMethod="GET",
        )
        return HttpResponseRedirect(redirect_to=presigned_url)

    return render(request, "projects/download_done.html", {"back_path": back_path})


def _week_starts_in_period(start_date: datetime.date, target_date: datetime.date) -> set[datetime.date]:
    """Every Monday from the Monday on or before `start_date` up to and including `target_date`."""
    week_starts = set()
    current = start_date - datetime.timedelta(days=start_date.weekday())
    while current <= target_date:
        week_starts.add(current)
        current += datetime.timedelta(days=7)
    return week_starts


def _project_effort_chart_data(project: KippoProject) -> dict:
    """Chart data for the project status page: per-user weekly and cumulative effort, and the expected-effort line."""
    effort_by_date = defaultdict(dict)
    for effort in ProjectWeeklyEffort.objects.filter(project=project).order_by("week_start", "user__first_name", "user__last_name"):
        effort_by_date[effort.week_start][effort.user.display_name] = effort.hours

    has_period = bool(project.start_date and project.target_date)
    week_starts = _week_starts_in_period(project.start_date, project.target_date) if has_period else set()
    # effort logged outside the project period is still shown
    week_starts.update(effort_by_date)
    sorted_dates = sorted(week_starts)
    sorted_users = sorted({user for efforts in effort_by_date.values() for user in efforts})

    # The expected-effort line is drawn inside the project period only (null outside it).
    dates_in_period = [date for date in sorted_dates if has_period and project.start_date <= date <= project.target_date]
    expected_hours = expected_effort_hours(project, dates_in_period)
    expected_effort = [(expected_hours[date] or 0) if date in expected_hours else None for date in sorted_dates]

    # Cumulative and weekly effort per user. Future weeks are null.
    today = timezone.localdate()
    effort_by_user = {}
    weekly_effort_by_user = {}
    total_effort_by_user = {}
    for user in sorted_users:
        effort_by_user[user] = []
        weekly_effort_by_user[user] = []
        cumulative_hours = 0
        for date in sorted_dates:
            hours = effort_by_date[date].get(user, 0)
            cumulative_hours += hours
            effort_by_user[user].append(cumulative_hours if date <= today else None)
            weekly_effort_by_user[user].append(hours if date <= today else None)
        total_effort_by_user[user] = cumulative_hours

    return {
        "labels": [date.isoformat() for date in sorted_dates],
        "users": sorted_users,
        "effort_by_user": effort_by_user,
        "weekly_effort_by_user": weekly_effort_by_user,
        "expected_effort": expected_effort,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "target_date": project.target_date.isoformat() if project.target_date else None,
        "allocated_hours": project.allocated_effort_hours,
        "pie_chart": {
            "users": sorted_users,
            "effort": [total_effort_by_user[user] for user in sorted_users],
        },
    }


PROJECT_STATUS_VERBOSE_NAME_FIELDS = ("organization", "phase", "start_date", "target_date", "allocated_staff_days", "project_manager")


@staff_member_required
def get_projectstatus_details(request: DjangoRequest, project_id: str) -> HttpResponse:
    """
    Display project status details with a stacked bar chart showing effort over time.

    The chart shows:
    - Stacked bars of actual effort hours by user per date
    - Expected effort line from start to end date

    Restricted to users belonging to the project's organization.
    """
    try:
        _, user_organizations = get_user_session_organization(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e.args))

    # Get the project and verify organization access
    try:
        project = KippoProject.objects.get(id=project_id, organization__in=user_organizations)
    except KippoProject.DoesNotExist:
        return HttpResponseBadRequest(f"Project with id {project_id} not found or access denied")

    project_progress_status = project.get_projectprogressstatus_values()
    remaining_hours = None
    if project_progress_status.allocated_effort_hours is not None and project_progress_status.current_effort_hours is not None:
        remaining_hours = project_progress_status.allocated_effort_hours - project_progress_status.current_effort_hours

    verbose_names = {field: project._meta.get_field(field).verbose_name for field in PROJECT_STATUS_VERBOSE_NAME_FIELDS}
    verbose_names["model"] = project._meta.verbose_name

    context = {
        "project": project,
        # json.dumps so None becomes null in the template
        "chart_data_json": json.dumps(_project_effort_chart_data(project)),
        "verbose_names": verbose_names,
        "project_progress_status": project_progress_status,
        "effort_meter": effort_meter(project_progress_status),
        "remaining_hours": remaining_hours,
        "URL_PREFIX": settings.URL_PREFIX,
        "ui_project_status_url": ui_url("project-status"),
    }

    return render(request, "projects/projectstatus_details.html", context)


class PublicTokenObtainPairView(TokenObtainPairView):
    """JWT token obtain endpoint (public - no authentication required)."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["auth"],
        summary="Obtain JWT token pair",
        description="Takes username and password credentials and returns an access and refresh JWT token pair.",
        auth=[],  # No authentication required
    )
    def post(self, request: Request, *args, **kwargs) -> Response:
        return super().post(request, *args, **kwargs)


class PublicTokenRefreshView(TokenRefreshView):
    """JWT token refresh endpoint (public - no authentication required)."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["auth"],
        summary="Refresh JWT access token",
        description="Takes a refresh token and returns a new access token if the refresh token is valid.",
        auth=[],  # No authentication required
    )
    def post(self, request: Request, *args, **kwargs) -> Response:
        return super().post(request, *args, **kwargs)


class CurrentUserView(APIView):
    """Return the current authenticated user info.

    Works with both session authentication (Django admin login) and JWT authentication.
    """

    permission_classes = [IsAuthenticated]

    def initial(self, request: Request, *args, **kwargs) -> None:
        # TODO: remove after diagnosing /api/auth/me/ always-401 (see PR fix/auth-me-redirect-loop).
        # Logs whether the session cookie arrived at Lambda and whether Django's
        # AuthenticationMiddleware resolved it to an authenticated user.
        django_request = request._request  # noqa: SLF001
        django_user = django_request.user
        cookie_keys = sorted(django_request.COOKIES.keys())
        logger.warning(
            "auth-me-debug django_user_authenticated=%s django_user=%s sessionid_present=%s "
            "session_key_set=%s authorization_header_present=%s cookie_keys=%s origin=%s referer=%s",
            django_user.is_authenticated,
            getattr(django_user, "username", None) or str(django_user),
            "sessionid" in django_request.COOKIES,
            bool(django_request.session.session_key),
            "HTTP_AUTHORIZATION" in django_request.META,
            cookie_keys,
            django_request.META.get("HTTP_ORIGIN", ""),
            django_request.META.get("HTTP_REFERER", ""),
        )
        super().initial(request, *args, **kwargs)

    @extend_schema(
        tags=["auth"],
        summary="Get current user",
        description="Returns the currently authenticated user's information. Works with both session and JWT auth.",
        responses={
            200: {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "email": {"type": "string"},
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "is_staff": {"type": "boolean"},
                },
            },
        },
    )
    def get(self, request: Request) -> Response:
        user = request.user
        return Response(
            {
                "username": user.username,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "is_staff": user.is_staff,
            }
        )


class SessionTokenView(APIView):
    """Issue JWT tokens to session-authenticated users.

    Allows users who logged in via SSO (Google, GitHub, etc.) to obtain
    JWT tokens without providing username/password credentials.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["auth"],
        summary="Get JWT tokens from session",
        description="Issues JWT access and refresh tokens to users authenticated via Django session (SSO login).",
        responses={
            200: {
                "type": "object",
                "properties": {
                    "access": {"type": "string", "description": "JWT access token"},
                    "refresh": {"type": "string", "description": "JWT refresh token"},
                },
            },
        },
    )
    def get(self, request: Request) -> Response:
        refresh = RefreshToken.for_user(request.user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            }
        )


class WeeklyEffortMissingWeeksView(APIView):
    """Return weeks where the current user has not entered weekly effort data.

    Calculates missing weeks from the start of the user's organization's fiscal year
    up to the current date, excluding the current week.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["weekly-effort"],
        summary="Get missing weekly effort weeks",
        description="Returns week start dates where the current user has not entered any weekly effort data.",
        responses={
            200: {
                "type": "object",
                "properties": {
                    "missing_weeks": {
                        "type": "array",
                        "items": {"type": "string", "format": "date"},
                        "description": "List of week start dates (Mondays) with no entries",
                    },
                    "fiscal_year_start": {"type": "string", "format": "date"},
                    "organization": {"type": "string", "nullable": True},
                },
            },
        },
    )
    def get(self, request: Request) -> Response:
        from datetime import date, timedelta

        from accounts.functions import get_allholiday_weekstarts

        from .models import ProjectWeeklyEffort

        user = request.user
        user_first_org = user.organizations.first()

        if not user_first_org:
            return Response(
                {
                    "missing_weeks": [],
                    "fiscal_year_start": None,
                    "organization": None,
                }
            )

        # Calculate fiscal year start date
        now = timezone.now()
        if now.month < user_first_org.fiscalyear_start_month:
            fiscal_year_start = date(now.year - 1, user_first_org.fiscalyear_start_month, 1)
        else:
            fiscal_year_start = date(now.year, user_first_org.fiscalyear_start_month, 1)

        # Get all week_starts where user has entries since fiscal year start
        user_weekstarts = set(
            ProjectWeeklyEffort.objects.filter(
                user=user,
                week_start__gte=fiscal_year_start,
                project__organization=user_first_org,
            ).values_list("week_start", flat=True)
        )

        # Generate all Mondays from fiscal year start to now
        all_mondays = []
        current = fiscal_year_start
        while current.weekday() != 0:  # Find first Monday
            current += timedelta(days=1)
        while current <= now.date():
            all_mondays.append(current)
            current += timedelta(days=7)

        # Calculate current week's Monday to exclude
        this_week_start = now.date()
        while this_week_start.weekday() != 0:
            this_week_start -= timedelta(days=1)

        # Weeks where every committed weekday is a holiday (personal/public) have nothing to enter
        org_membership = user.get_membership(organization=user_first_org)
        allholiday_weekstarts = get_allholiday_weekstarts(
            [org_membership] if org_membership else [],
            all_mondays,
            default_holiday_country=user_first_org.default_holiday_country,
        ).get(user.pk, set())

        # Find missing weeks (excluding current week and all-holiday weeks)
        candidate_missing = sorted(set(all_mondays) - user_weekstarts)
        missing_weeks = [week.isoformat() for week in candidate_missing if week != this_week_start and week not in allholiday_weekstarts]

        return Response(
            {
                "missing_weeks": missing_weeks,
                "fiscal_year_start": fiscal_year_start.isoformat(),
                "organization": user_first_org.name,
            }
        )


class WeeklyEffortExpectedHoursView(APIView):
    """Calculate expected working hours for a user for a given week.

    Takes into account:
    - User's committed workdays from OrganizationMembership
    - Organization's day_workhours setting
    - Public holidays in the target week
    - Personal holidays in the target week
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["weekly-effort"],
        summary="Get expected hours for a week",
        description="Calculate expected working hours for the current user for a given week.",
        parameters=[
            OpenApiParameter(
                name="week_start",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Start date of week (YYYY-MM-DD format, must be a Monday)",
            ),
        ],
        responses={
            200: {
                "type": "object",
                "properties": {
                    "expected_hours": {"type": "number"},
                    "week_start": {"type": "string", "format": "date"},
                    "organization": {"type": "string", "nullable": True},
                },
            },
            400: {
                "type": "object",
                "properties": {
                    "error": {"type": "string"},
                },
            },
        },
    )
    def get(self, request: Request) -> Response:
        from datetime import date

        from accounts.models import PersonalHoliday, PublicHoliday
        from commons.context_processors import get_personal_holiday_hours
        from commons.definitions import PERSONAL_HOLIDAY_LOOKBACK_DAYS

        week_start_str = request.query_params.get("week_start")

        if not week_start_str:
            return Response(
                {"error": "week_start parameter is required (format: YYYY-MM-DD)"},
                status=400,
            )

        try:
            week_start = date.fromisoformat(week_start_str)
        except ValueError:
            return Response(
                {"error": "Invalid week_start format. Use YYYY-MM-DD"},
                status=400,
            )

        # Validate that week_start is a Monday
        if week_start.weekday() != 0:
            return Response(
                {"error": "week_start must be a Monday"},
                status=400,
            )

        user = request.user
        user_first_org = user.organizations.first()

        if not user_first_org:
            return Response(
                {
                    "expected_hours": 0,
                    "week_start": week_start.isoformat(),
                    "organization": None,
                }
            )

        org_membership = user.get_membership(organization=user_first_org)
        if not org_membership:
            return Response(
                {
                    "expected_hours": 0,
                    "week_start": week_start.isoformat(),
                    "organization": user_first_org.name,
                }
            )

        # Calculate base expected hours: committed_days × day_workhours
        committed_days = org_membership.committed_days
        expected_hours = committed_days * user_first_org.day_workhours

        # Calculate week end date (Friday)
        week_enddate = week_start + timezone.timedelta(days=4)

        # Subtract public holidays
        public_holidays = PublicHoliday.objects.filter(
            day__gte=week_start,
            day__lte=week_enddate,
        )
        if user.holiday_country:
            public_holidays = public_holidays.filter(country=user.holiday_country)
        elif org_membership.organization.default_holiday_country:
            public_holidays = public_holidays.filter(country=org_membership.organization.default_holiday_country)

        public_holiday_dates = set(public_holidays.values_list("day", flat=True))
        public_holiday_hours = len(public_holiday_dates) * user_first_org.day_workhours
        expected_hours -= public_holiday_hours

        # Subtract personal holidays.
        # NOTE: filtered from a lookback -- the filter matches the holiday's START date, so a
        # span beginning before the week must be included for its in-week days to be deducted.
        personal_holidays = PersonalHoliday.objects.filter(
            user=user,
            day__gte=week_start - timezone.timedelta(days=PERSONAL_HOLIDAY_LOOKBACK_DAYS),
            day__lte=week_enddate,
        )
        personal_holiday_hours = get_personal_holiday_hours(
            personal_holidays,
            day_workhours=user_first_org.day_workhours,
            start_date=week_start,
            end_date=week_enddate,
            exclude_dates=public_holiday_dates,
        )
        expected_hours -= personal_holiday_hours

        return Response(
            {
                "expected_hours": max(expected_hours, 0),
                "week_start": week_start.isoformat(),
                "organization": user_first_org.name,
            }
        )
