import logging
import urllib.parse
from collections import Counter, namedtuple
from typing import List, Optional, Tuple

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from tasks.exceptions import ProjectConfigurationError
from tasks.functions import prepare_project_engineering_load_plot_data
from tasks.models import KippoTask, KippoTaskStatus

from kippo.aws import S3_CLIENT, s3_key_exists

from .charts.functions import prepare_burndown_chart_components
from .exceptions import ProjectDatesError, TaskStatusError
from .functions import get_user_session_organization
from .models import KippoMilestone, KippoProject

logger = logging.getLogger(__name__)


AssigneeStatus = namedtuple("AssigneeStatus", ("assignee", "task_count", "available_workdays", "estimated_workdays", "load_percentage"))


def project_assignee_keyfunc(task: KippoTask) -> tuple:
    """
    A keying function that returns the values to use for sorting
    """
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


def _get_task_details(active_taskstatus: List[KippoTaskStatus]) -> Tuple[List[int], List[KippoTask]]:
    collected_task_ids = []
    unique_tasks = []
    for taskstatus in active_taskstatus:
        if taskstatus.task.id not in collected_task_ids:
            unique_tasks.append(taskstatus.task)
            collected_task_ids.append(taskstatus.task.id)
    return collected_task_ids, unique_tasks


@staff_member_required
def view_inprogress_projects_status(request: HttpRequest) -> HttpResponse:
    warning = None

    try:
        selected_organization, user_organizations = get_user_session_organization(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e.args))

    slug = request.GET.get("slug", None)
    if slug:
        project = get_object_or_404(KippoProject, slug=slug, organization=selected_organization)
        projects = [project]
    else:
        projects = KippoProject.objects.filter(is_closed=False, organization=selected_organization)
    active_projects = KippoProject.objects.filter(is_closed=False, organization=selected_organization).order_by("name")

    # Collect KippoTaskStatus for projects
    active_taskstatus = []
    all_has_estimates = False
    for project in projects:
        project_active_taskstatus, has_estimates = project.get_active_taskstatus()
        if has_estimates:
            all_has_estimates = True
        active_taskstatus.extend(project_active_taskstatus)

    if not all_has_estimates:
        msg = 'No Estimates defined in tasks (Expect "estimate labels")'
        messages.add_message(request, messages.WARNING, msg)

    project = None
    script = None
    div = None
    latest_effort_date = None
    if slug:
        assert len(projects) == 1
        project = projects[0]
        # generate burn-down chart
        try:
            script, div = prepare_burndown_chart_components(project)
        except TaskStatusError as e:
            warning = f"Data not available for project({project.name}): {e.args}"
            messages.add_message(request, messages.WARNING, warning)
            logger.warning(warning)
        except ProjectDatesError as e:
            warning = f"start_date or target_date not set for project: {e.args}"
            messages.add_message(request, messages.WARNING, warning)
            logger.warning(warning)
    else:
        # show project schedule chart
        if not selected_organization:
            return HttpResponseBadRequest("KippoUser not registered with an Organization!")

        # check projects for start_date, target_date
        projects_missing_dates = KippoProject.objects.filter(Q(start_date__isnull=True) | Q(target_date__isnull=True))
        projects_missing_dates = projects_missing_dates.filter(
            organization=selected_organization, github_project_api_url__isnull=False, is_closed=False
        )
        if projects_missing_dates:
            for p in projects_missing_dates:
                warning = (
                    f"Project({p.name}) start_date({p.start_date}) or target_date({p.target_date}) not defined! " f"(Will not be displayed in chart) "
                )
                messages.add_message(request, messages.WARNING, warning)
                logger.warning(warning)
        try:
            (script, div), latest_effort_date = prepare_project_engineering_load_plot_data(selected_organization)
            logger.debug(f"latest_effort_date: {latest_effort_date}")
        except ProjectConfigurationError as e:
            logger.warning(f"No projects with start_date or target_date defined: {e.args}")
        except ValueError as e:
            logger.exception(e)
            logger.error(str(e.args))
            error = f"Unable to process tasks: {e.args}"
            messages.add_message(request, messages.ERROR, error)

    # collect unique Tasks
    collected_task_ids, unique_tasks = _get_task_details(active_taskstatus)

    # get user totals
    user_effort_totals = Counter()
    for task in unique_tasks:
        if task.assignee:
            days_remaining = task.effort_days_remaining() if task.effort_days_remaining() else 0
            user_effort_totals[task.assignee.username] += days_remaining

    # sort tasks by assignee.username, project.name
    sorted_tasks = sorted(unique_tasks, key=project_assignee_keyfunc)
    context = {
        "project": project,
        "tasks": sorted_tasks,
        "user_effort_totals": dict(user_effort_totals),
        "chart_script": script,
        "chart_div": div,
        "latest_effort_date": latest_effort_date,
        "active_projects": active_projects,
        "messages": messages.get_messages(request),
        "selected_organization": selected_organization,
        "organizations": user_organizations,
    }

    return render(request, "projects/view_inprogress_projects_status.html", context)


@staff_member_required
def set_user_session_organization(request, organization_id: str = None) -> HttpResponse:
    user_organizations = list(request.user.organizations)
    if not organization_id:
        return HttpResponseBadRequest('required "organization_id" not given!')
    elif not user_organizations:
        return HttpResponseBadRequest(f"user({request.user.username}) has no OrganizationMemberships!")

    elif organization_id not in [str(o.id) for o in user_organizations]:
        logger.debug(f"Invalid organization_id({organization_id}) for user({request.user.username}) using user first")
        organization_id = user_organizations[0].id

    request.session["organization_id"] = str(organization_id)
    logger.debug(f'setting session["organization_id"] for user({request.user.username}): {organization_id}')
    return HttpResponseRedirect(f"{settings.URL_PREFIX}/projects/")  # go reload the page with the set org


def _get_milestone_assignee_status(milestone: KippoMilestone) -> List[AssigneeStatus]:
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
def view_milestone_status(request: HttpRequest, milestone_id: Optional[str] = None) -> HttpResponse:
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
            request, messages.ERROR, "No KippoTaskStatus Items defined For Organization Projects -- Unable to prepare Milestone Data!"
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
def data_download_waiter(request):
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
def data_download_done(request):
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
        print(presigned_url)
        return HttpResponseRedirect(redirect_to=presigned_url)

    return render(request, "projects/download_done.html", {"back_path": back_path})
