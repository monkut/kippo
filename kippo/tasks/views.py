import logging
from collections import Counter

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from projects.functions import get_user_session_organization
from projects.models import KippoProject

from .exceptions import ProjectConfigurationError
from .functions import prepare_project_engineering_load_plot_data
from .models import KippoTask, KippoTaskStatus

logger = logging.getLogger(__name__)

# TODO: Fix support
# -- Initially used to separate general meeting tasks from development tasks
# -- Consider how this should be handled moving forward (general meeting tasks are not being managed atm)
EXCLUDE_TASK_CATEGORIES = []
DEFAULT_TASK_DISPLAY_STATE = settings.DEFAULT_TASK_DISPLAY_STATE


def assignee_project_keyfunc(task_object: KippoTask) -> tuple:
    """
    A keying function that returns the values to use for sorting
    :param task_object:
    :return: (task_object.assignee.username, task_object.project.name)
    """
    username = ""
    if task_object.assignee:
        username = task_object.assignee.username

    project = ""
    if task_object.project:
        project = task_object.project.name

    return username, project


@staff_member_required
def view_inprogress_task_status(request):
    github_login = request.GET.get("github_login", None)

    # Collect tasks with TaskStatus updated this last 2 weeks
    two_weeks_ago = timezone.timedelta(days=14)
    active_taskstatus_startdate = (timezone.now() - two_weeks_ago).date()

    try:
        selected_organization, user_organizations = get_user_session_organization(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e.args))
    active_projects = KippoProject.objects.filter(is_closed=False, organization=selected_organization).order_by("name")

    additional_filters = {}
    if github_login:
        additional_filters["task__assignee__github_login"] = github_login

    active_taskstatus = []
    for project in KippoProject.objects.filter(is_closed=False):
        project_active_taskstatuses, _ = project.get_active_taskstatus(additional_filters=additional_filters)
        active_taskstatus.extend(project_active_taskstatuses)

    task_state_counts = {
        r["state"]: r["state__count"]
        for r in KippoTaskStatus.objects.filter(effort_date__gte=active_taskstatus_startdate)
        .values("state")
        .order_by("state")
        .annotate(Count("state"))
    }
    total_state_count = sum(task_state_counts.values())
    task_state_counts["total"] = total_state_count

    # apply specific user filter if defined
    script = None
    div = None
    latest_effort_date = None
    if github_login:
        try:
            (script, div), latest_effort_date = prepare_project_engineering_load_plot_data(selected_organization, assignee_filter=github_login)
        except ProjectConfigurationError as e:
            logger.error(f"ProjectConfigurationError: ({e.args}): {request.build_absolute_uri()}")
            msg = f"ProjectConfigurationError: {e.args}"
            messages.add_message(request, messages.ERROR, msg)

    # collect unique Tasks
    collected_task_ids = []
    unique_tasks = []
    for taskstatus in active_taskstatus:
        if taskstatus.task.id not in collected_task_ids:
            unique_tasks.append(taskstatus.task)
            collected_task_ids.append(taskstatus.task.id)

    # get user totals
    user_effort_totals = Counter()
    for task in unique_tasks:
        if task.assignee:
            days_remaining = task.effort_days_remaining() if task.effort_days_remaining() else 0
            user_effort_totals[task.assignee.username] += days_remaining

    # sort tasks by assignee.username, project.name
    sorted_tasks = sorted(unique_tasks, key=assignee_project_keyfunc)
    context = {
        "tasks": sorted_tasks,
        "active_projects": active_projects,
        "user_effort_totals": dict(user_effort_totals),
        "task_state_counts": task_state_counts,
        "chart_script": script,
        "chart_div": div,
        "latest_effort_date": latest_effort_date,
        "messages": messages.get_messages(request),
    }

    return render(request, "tasks/view_inprogress_task_status.html", context)
