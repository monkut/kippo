import json
import logging
import urllib.parse
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections import Counter

# from tasks.functions import prepare_project_engineering_load_plot_data
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
from tasks.models import KippoTask, KippoTaskStatus

from kippo.awsclients import S3_CLIENT, s3_key_exists

# from .charts.functions import prepare_burndown_chart_components
from .functions import get_user_session_organization
from .models import KippoMilestone, KippoProject

logger = logging.getLogger(__name__)


class AssigneeStatus(NamedTuple):
    assignee: str | None
    task_count: int
    available_workdays: int
    estimated_workdays: int
    load_percentage: str


def project_assignee_keyfunc(task: KippoTask) -> tuple:
    """A keying function that returns the values to use for sorting"""
    username = ""
    if task.assignee:
        username = task.assignee.username

    project = ""
    if task.project:
        project = task.project.name

    milestone = ""
    if task.milestone:
        milestone = task.milestone.target_date.isoformat()

    return project, username, milestone


def _get_task_details(active_taskstatus: list[KippoTaskStatus]) -> tuple[list[int], list[KippoTask]]:
    collected_task_ids = []
    unique_tasks = []
    for taskstatus in active_taskstatus:
        if taskstatus.task.id not in collected_task_ids:
            unique_tasks.append(taskstatus.task)
            collected_task_ids.append(taskstatus.task.id)
    return collected_task_ids, unique_tasks


#
# @staff_member_required
# def view_inprogress_projects_status(request: HttpRequest) -> HttpResponse:
#     warning = None
#
#     try:
#         selected_organization, user_organizations = get_user_session_organization(request)
#     except ValueError as e:
#         return HttpResponseBadRequest(str(e.args))
#
#     slug = request.GET.get("slug", None)
#     if slug:
#         project = get_object_or_404(KippoProject, slug=slug, organization=selected_organization)
#         projects = [project]
#     else:
#         projects = KippoProject.objects.filter(is_closed=False, organization=selected_organization)
#     active_projects = KippoProject.objects.filter(is_closed=False, organization=selected_organization).order_by("name")
#
#     # Collect KippoTaskStatus for projects
#     active_taskstatus = []
#     all_has_estimates = False
#     for project in projects:
#         project_active_taskstatus, has_estimates = project.get_active_taskstatus()
#         if has_estimates:
#             all_has_estimates = True
#         active_taskstatus.extend(project_active_taskstatus)
#
#     if not all_has_estimates:
#         msg = 'No Estimates defined in tasks (Expect "estimate labels")'
#         messages.add_message(request, messages.WARNING, msg)
#
#     project = None
#     script = None
#     div = None
#     latest_effort_date = None
#     if slug:
#         assert len(projects) == 1
#         project = projects[0]
#         # generate burn-down chart
#         try:
#             script, div = prepare_burndown_chart_components(project)
#         except TaskStatusError as e:
#             warning = f"Data not available for project({project.name}): {e.args}"
#             messages.add_message(request, messages.WARNING, warning)
#             logger.warning(warning)
#         except ProjectDatesError as e:
#             warning = f"start_date or target_date not set for project: {e.args}"
#             messages.add_message(request, messages.WARNING, warning)
#             logger.warning(warning)
#     else:
#         # show project schedule chart
#         if not selected_organization:
#             return HttpResponseBadRequest("KippoUser not registered with an Organization!")
#
#         # check projects for start_date, target_date
#         projects_missing_dates = KippoProject.objects.filter(Q(start_date__isnull=True) | Q(target_date__isnull=True))
#         projects_missing_dates = projects_missing_dates.filter(
#             organization=selected_organization, github_project_api_url__isnull=False, is_closed=False
#         )
#         if projects_missing_dates:
#             for p in projects_missing_dates:
#                 warning = (
#                     f"Project({p.name}) start_date({p.start_date}) or target_date({p.target_date}) not defined! "
#                     f"(Will not be displayed in chart) "
#                 )
#                 messages.add_message(request, messages.WARNING, warning)
#                 logger.warning(warning)
#         try:
#             (script, div), latest_effort_date = prepare_project_engineering_load_plot_data(selected_organization)
#             logger.debug(f"latest_effort_date: {latest_effort_date}")
#         except ProjectConfigurationError as e:
#             logger.warning(f"No projects with start_date or target_date defined: {e.args}")
#         except ValueError as e:
#             logger.exception(e)
#             logger.error(str(e.args))
#             error = f"Unable to process tasks: {e.args}"
#             messages.add_message(request, messages.ERROR, error)
#
#     # collect unique Tasks
#     collected_task_ids, unique_tasks = _get_task_details(active_taskstatus)
#
#     # get user totals
#     user_effort_totals = Counter()
#     for task in unique_tasks:
#         if task.assignee:
#             days_remaining = task.effort_days_remaining() if task.effort_days_remaining() else 0
#             user_effort_totals[task.assignee.username] += days_remaining
#
#     # sort tasks by assignee.username, project.name
#     sorted_tasks = sorted(unique_tasks, key=project_assignee_keyfunc)
#     context = {
#         "project": project,
#         "tasks": sorted_tasks,
#         "user_effort_totals": dict(user_effort_totals),
#         "chart_script": script,
#         "chart_div": div,
#         "latest_effort_date": latest_effort_date,
#         "active_projects": active_projects,
#         "messages": messages.get_messages(request),
#         "selected_organization": selected_organization,
#         "organizations": user_organizations,
#     }
#
#     return render(request, "projects/view_inprogress_projects_status.html", context)


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
        presigned_url = S3_CLIENT.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": settings.DUMPDATA_S3_BUCKETNAME, "Key": filename},
            ExpiresIn=expired_seconds,
            HttpMethod="GET",
        )
        return HttpResponseRedirect(redirect_to=presigned_url)

    return render(request, "projects/download_done.html", {"back_path": back_path})


@staff_member_required
def get_projectstatus_details(request: DjangoRequest, project_id: str) -> HttpResponse:  # noqa: PLR0915, C901, PLR0912
    """
    Display project status details with a stacked bar chart showing effort over time.

    The chart shows:
    - Stacked bars of actual effort hours by user per date
    - Expected effort line from start to end date

    Restricted to users belonging to the project's organization.
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
    import datetime
    from collections import defaultdict

    from commons.definitions import MONDAY

    from .models import ProjectWeeklyEffort

    project_weekly_efforts = ProjectWeeklyEffort.objects.filter(project=project).order_by("week_start", "user__first_name", "user__last_name")

    # Prepare data for the chart
    # Group by date and collect user efforts
    effort_by_date = defaultdict(dict)
    all_users = set()

    for effort in project_weekly_efforts:
        effort_by_date[effort.week_start][effort.user.display_name] = effort.hours
        all_users.add(effort.user.display_name)

    # Generate all week start dates from start_date to target_date
    all_week_starts = set()
    if project.start_date and project.target_date:
        # Find the Monday on or before start_date
        current = project.start_date
        while current.weekday() != MONDAY:
            current -= datetime.timedelta(days=1)

        # Generate all Mondays up to and including target_date
        while current <= project.target_date:
            all_week_starts.add(current)
            current += datetime.timedelta(days=7)

    # Include all dates from effort_by_date (to show effort beyond target_date)
    all_week_starts.update(effort_by_date.keys())

    # Sort dates and users for consistent ordering
    sorted_dates = sorted(all_week_starts)
    sorted_users = sorted(all_users)

    # Calculate expected effort for each date
    # Only calculate expected effort for dates within start_date to target_date range
    expected_effort_data = []
    for date in sorted_dates:
        if project.start_date and project.target_date and project.start_date <= date <= project.target_date:
            _, expected_hours = project.get_expected_effort(at_date=date)
            expected_effort_data.append(
                {
                    "date": date.isoformat(),
                    "expected_hours": expected_hours if expected_hours else 0,
                }
            )
        else:
            # No expected effort outside the project date range
            expected_effort_data.append(
                {
                    "date": date.isoformat(),
                    "expected_hours": None,  # null for dates outside range
                }
            )

    # Prepare chart data structure
    chart_data = {
        "labels": [date.isoformat() for date in sorted_dates],
        "users": sorted_users,
        "effort_by_user": {},
        "weekly_effort_by_user": {},
        "expected_effort": [item["expected_hours"] for item in expected_effort_data],
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "target_date": project.target_date.isoformat() if project.target_date else None,
        "allocated_hours": project.allocated_effort_hours,
    }

    # Fill in effort data for each user (cumulative and weekly)
    # Do not show cumulative effort for future dates
    today = timezone.now().date()
    latest_cumulative_effort = {}
    for user in sorted_users:
        chart_data["effort_by_user"][user] = []
        chart_data["weekly_effort_by_user"][user] = []
        cumulative_hours = 0
        for date in sorted_dates:
            hours = effort_by_date[date].get(user, 0)
            cumulative_hours += hours
            # Only show cumulative effort for dates up to today
            if date <= today:
                chart_data["effort_by_user"][user].append(cumulative_hours)
                chart_data["weekly_effort_by_user"][user].append(hours)
            else:
                # Future dates: show null for cumulative and weekly effort
                chart_data["effort_by_user"][user].append(None)
                chart_data["weekly_effort_by_user"][user].append(None)
        # Store the latest (final) cumulative value for pie chart
        latest_cumulative_effort[user] = cumulative_hours

    # Add pie chart data (percentage of effort per user)
    chart_data["pie_chart"] = {
        "users": sorted_users,
        "effort": [latest_cumulative_effort[user] for user in sorted_users],
    }

    # Get verbose names from model
    verbose_names = {
        "model": project._meta.verbose_name,
        "organization": project._meta.get_field("organization").verbose_name,
        "phase": project._meta.get_field("phase").verbose_name,
        "start_date": project._meta.get_field("start_date").verbose_name,
        "target_date": project._meta.get_field("target_date").verbose_name,
        "allocated_staff_days": project._meta.get_field("allocated_staff_days").verbose_name,
        "project_manager": project._meta.get_field("project_manager").verbose_name,
    }

    # Get project progress status for meter display
    project_progress_status = project.get_projectprogressstatus_values()

    # Calculate meter values for work status display (matching admin logic)
    meter_values = None
    if project_progress_status.allocated_effort_hours and project_progress_status.expected_effort_hours:
        low = int(project_progress_status.expected_effort_hours) + 1
        high = (
            settings.PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD / 100
        ) * project_progress_status.expected_effort_hours + project_progress_status.expected_effort_hours

        max_value = project_progress_status.allocated_effort_hours
        if (
            project_progress_status.allocated_effort_hours
            and project_progress_status.current_effort_hours
            and project_progress_status.allocated_effort_hours < project_progress_status.current_effort_hours
        ):
            max_value = project_progress_status.current_effort_hours

        meter_values = {
            "low": low,
            "optimum": int(project_progress_status.expected_effort_hours),
            "high": int(high),
            "max": max_value,
            "value": project_progress_status.current_effort_hours,
            "show_low": low < max_value,  # Only show low if it's less than max
        }

    # Calculate remaining hours
    remaining_hours = None
    if project_progress_status.allocated_effort_hours is not None and project_progress_status.current_effort_hours is not None:
        remaining_hours = project_progress_status.allocated_effort_hours - project_progress_status.current_effort_hours

    # Convert chart_data to JSON to properly handle None -> null conversion
    chart_data_json = json.dumps(chart_data)

    context = {
        "project": project,
        "chart_data_json": chart_data_json,
        "verbose_names": verbose_names,
        "project_progress_status": project_progress_status,
        "meter_values": meter_values,
        "remaining_hours": remaining_hours,
        "selected_organization": selected_organization,
        "organizations": user_organizations,
        "URL_PREFIX": settings.URL_PREFIX,
        "PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD": settings.PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD,
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

        from accounts.models import PersonalHoliday, PublicHoliday

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

        # Get user's committed weekdays and holiday data for filtering all-holiday weeks
        org_membership = user.get_membership(organization=user_first_org)
        committed_weekdays = org_membership.committed_weekdays if org_membership else list(range(5))  # default Mon-Fri

        # Get holiday country for public holiday lookup
        holiday_country = user.holiday_country or (user_first_org.default_holiday_country if user_first_org else None)

        # Get public holidays in the fiscal year range
        public_holiday_dates = set()
        if holiday_country:
            public_holiday_dates = set(
                PublicHoliday.objects.filter(
                    country=holiday_country,
                    day__gte=fiscal_year_start,
                    day__lte=now.date(),
                ).values_list("day", flat=True)
            )

        # Get personal holidays in the fiscal year range (expand multi-day holidays)
        personal_holiday_dates = set()
        personal_holidays = PersonalHoliday.objects.filter(
            user=user,
            day__gte=fiscal_year_start - timedelta(days=365),  # Include holidays that may span into range
            day__lte=now.date(),
        )
        for holiday in personal_holidays:
            for day_offset in range(holiday.duration):
                holiday_date = holiday.day + timedelta(days=day_offset)
                if fiscal_year_start <= holiday_date <= now.date():
                    personal_holiday_dates.add(holiday_date)

        all_holiday_dates = public_holiday_dates | personal_holiday_dates

        def is_all_holiday_week(week_start: date) -> bool:
            """Check if all committed weekdays in the week are holidays."""
            for weekday in committed_weekdays:
                day_in_week = week_start + timedelta(days=weekday)
                if day_in_week not in all_holiday_dates:
                    return False
            return True

        # Find missing weeks (excluding current week and all-holiday weeks)
        candidate_missing = sorted(set(all_mondays) - user_weekstarts)
        missing_weeks = [week.isoformat() for week in candidate_missing if week != this_week_start and not is_all_holiday_week(week)]

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

        public_holiday_days = public_holidays.count()
        public_holiday_hours = public_holiday_days * user_first_org.day_workhours
        expected_hours -= public_holiday_hours

        # Subtract personal holidays
        personal_holidays = PersonalHoliday.objects.filter(
            user=user,
            day__gte=week_start,
            day__lte=week_enddate,
        )
        personal_holiday_hours = get_personal_holiday_hours(
            personal_holidays,
            day_workhours=user_first_org.day_workhours,
            end_date=week_enddate,
        )
        expected_hours -= personal_holiday_hours

        return Response(
            {
                "expected_hours": max(expected_hours, 0),
                "week_start": week_start.isoformat(),
                "organization": user_first_org.name,
            }
        )
