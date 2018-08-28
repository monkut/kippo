import logging
from collections import Counter

from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required

from tasks.models import KippoTask, KippoTaskStatus
from .charts.functions import prepare_burndown_chart_components
from .models import ActiveKippoProject, KippoProject
from .exceptions import TaskStatusError, ProjectDatesError


logger = logging.getLogger(__name__)


def project_assignee_keyfunc(task_object: KippoTask) -> tuple:
    """
    A keying function that returns the values to use for sorting

    :param task_object: KippoTask task object
    :return: (task_object.assignee.username, task_object.project.name)
    """
    username = ''
    if task_object.assignee:
        username = task_object.assignee.username

    project = ''
    if task_object.project:
        project = task_object.project.name

    return project, username


def view_projects_schedule(request, project_id=None):
    raise NotImplementedError()


@staff_member_required
def view_inprogress_projects_overview(request):
    now = timezone.now()

    # TODO: update so that the project 'types' are NOT hard coded/fixed
    inprogress_projects = ActiveKippoProject.objects.filter(start_date__lte=now)
    inprogress_consulting = inprogress_projects.filter(category='consulting')
    inprogress_poc = inprogress_projects.filter(category='poc')
    inprogress_production = inprogress_projects.filter(category='production')

    upcoming_projects = ActiveKippoProject.objects.filter(start_date__gt=now)
    upcoming_consulting = upcoming_projects.filter(category='consulting')
    upcoming_poc = upcoming_projects.filter(category='poc')
    upcoming_production = upcoming_projects.filter(category='production')

    context = {
        'inprogress_consulting': inprogress_consulting,
        'inprogress_poc': inprogress_poc,
        'inprogress_production': inprogress_production,
        'upcoming_consulting': upcoming_consulting,
        'upcoming_poc': upcoming_poc,
        'upcoming_production': upcoming_production,
    }
    return render(request, 'projects/view_inprogress_projects_status_overview.html', context)


def view_inprogress_projects_status(request):
    warning = None
    slug = request.GET.get('slug', None)
    if slug:
        project = get_object_or_404(KippoProject, slug=slug)
        projects = [project]
    else:
        projects = KippoProject.objects.filter(is_closed=False)
    active_projects = KippoProject.objects.filter(is_closed=False).order_by('name')

    # Collect tasks with TaskStatus updated this last 2 weeks
    two_weeks_ago = timezone.timedelta(days=14)
    active_taskstatus_startdate = (timezone.now() - two_weeks_ago).date()

    active_taskstatus = []
    for project in projects:
        done_column_names = project.columnset.get_done_column_names()
        results = KippoTaskStatus.objects.filter(effort_date__gte=active_taskstatus_startdate,
                                                 task__github_issue_api_url__isnull=False,  # filter out non-linked tasks
                                                 task__project=project).exclude(state__in=done_column_names)
        active_taskstatus.extend(list(results))

    project = None
    script = None
    div = None
    if slug:
        assert len(projects) == 1
        project = projects[0]
        # generate burndown chart
        try:
            script, div = prepare_burndown_chart_components(project)
        except TaskStatusError as e:
            warning = f'Data not available for project({project.name}): {e.args}'
            messages.add_message(request, messages.WARNING, warning)
            logger.warning(warning)
        except ProjectDatesError as e:
            warning = f'start_date or target_date not set for project: {e.args}'
            messages.add_message(request, messages.WARNING, warning)
            logger.warning(warning)

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
    sorted_tasks = sorted(unique_tasks, key=project_assignee_keyfunc)
    context = {
        'project': project,
        'tasks': sorted_tasks,
        'user_effort_totals': dict(user_effort_totals),
        'chart_script': script,
        'chart_div': div,
        'active_projects': active_projects,
        'messages': messages.get_messages(request),
    }

    return render(request, 'projects/view_inprogress_projects_status.html', context)
