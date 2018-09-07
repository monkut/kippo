import datetime
import logging
from collections import defaultdict
from typing import Dict, List, Any, Tuple

from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Value, Count
from django.db.models.functions import Coalesce

from qlu.core import QluTaskScheduler, QluTask, QluMilestone, QluTaskEstimates

from accounts.models import KippoUser, KippoOrganization
from projects.models import KippoProject, KippoMilestone
from .exceptions import ProjectConfigurationError
from .models import KippoTask, KippoTaskStatus
from .charts.functions import prepare_project_schedule_chart_components


logger = logging.getLogger(__name__)
TUESDAY_WEEKDAY = 2


def get_github_issue_estimate_label(issue, prefix=settings.DEFAULT_GITHUB_ISSUE_LABEL_ESTIMATE_PREFIX) -> int:
    """
    Parse the estimate label into an estimate value
    Estimate labels follow the scheme: {prefix}N{suffix}
    WHERE:
    - {prefix} estimate label identifier
    - N is a positive integer representing number of days
    - {suffix} one of ('d', 'day', 'days', 'h', 'hour', 'hours')

    .. note::

        Only integer values are supported.
        (fractional days are not represented at the moment)

    :param issue: github issue object
    :param prefix: This identifies the github issue label as being an estimate
    :return: parsed estimate result in days
    """
    estimate = None
    valid_label_suffixes = ('d', 'day', 'days', 'h', 'hour', 'hours')
    for label in issue.labels:
        if label.name.startswith(prefix):
            estimate_str_value = label.name.split(prefix)[-1]
            for suffix in valid_label_suffixes:
                if estimate_str_value.endswith(suffix):  # d = days, h = hours
                    estimate_str_value = estimate_str_value.split(suffix)[0]
            estimate = int(estimate_str_value)

            if label.name.endswith(('h', 'hour', 'hours')):
                # all estimates are normalized to days
                # if hours convert to a days
                estimate = int(round((estimate / settings.DAY_WORKHOURS) + .5, 0))

    return estimate


def get_github_issue_category_label(issue, prefix=settings.DEFAULT_GITHUB_ISSUE_LABEL_CATEGORY_PREFIX) -> str:
    """
    Parse the category label into the category value
    Category Labels follow the scheme:
        category:CATEGORY_NAME
        WHERE:
            CATEGORY_NAME should match the VALID_TASK_CATEGORIES value in models.py
    :param issue: github issue object
    :param prefix: This identifies the github issue label as being a category
    :return: parsed category result
    """
    category = None
    for label in issue.labels:
        if label.name.startswith(prefix):
            category = label.name.split(prefix)[-1].strip()
    return category


class ProjectDatesError(Exception):
    pass


class TaskStatusError(Exception):
    pass


def get_project_weekly_effort(project: KippoProject, current_date: datetime.date=None):
    """
    Obtain the project weekly effort
    :param project:
    :param current_date:
    :return:
    """
    if not current_date:
        current_date = timezone.now().date()
    elif isinstance(current_date, datetime.datetime):
        current_date = current_date.date()

    if not project.start_date or not project.target_date:
        raise ProjectDatesError(f'{project.name} required dates not set: start_date={project.start_date}, '
                                f'target_date={project.target_date}')

    # get latest effort status
    # -- only a single entry per date

    # prepare dates
    search_dates = []
    start_date_calendar_info = project.start_date.isocalendar()
    start_date_year, start_date_week, _ = start_date_calendar_info
    initial_week_start_date = datetime.datetime.strptime(f'{start_date_year}-{start_date_week}-{TUESDAY_WEEKDAY}', '%Y-%W-%w').date()
    current_week_start_date = initial_week_start_date

    while current_week_start_date <= project.target_date:
        search_dates.append(current_week_start_date)
        last_week_start_date = current_week_start_date
        current_week_start_date += datetime.timedelta(days=7)
        if last_week_start_date < current_date < current_week_start_date:
            # add the current date (to show the current status)
            search_dates.append(current_date)
    if project.target_date not in search_dates:
        search_dates.append(project.target_date)

    active_column_names = project.columnset.get_active_column_names()
    all_status_entries = []  # state__in=GITHUB_ACTIVE_TASK_STATES
    for current_week_start_date in search_dates:
        previous_status_entries = KippoTaskStatus.objects.filter(
            task__project=project,
            task__assignee__github_login__isnull=False,
            effort_date=current_week_start_date,
            state__in=active_column_names
        ).values(
            'task__project',
            'effort_date',
            'task__assignee__github_login'
        ).annotate(
            task_count=Count('task'),
            estimate_days_sum=Coalesce(Sum('estimate_days'), Value(0))
        )

        all_status_entries.extend(list(previous_status_entries))

    if not all_status_entries:
        raise TaskStatusError(f'No TaskStatus found for project({project.name}) in ranges: {project.start_date} to {project.target_date}')

    return all_status_entries, search_dates


def prepare_project_plot_data(project: KippoProject, current_date: datetime.date=None):
    """
    Format data for easy plotting
    :param project:
    :param current_date:
    :return:
    """
    data = defaultdict(list)
    burndown_line = None
    if project.start_date and project.target_date and project.allocated_staff_days:
        start_date = project.start_date.strftime('%Y-%m-%d (%a)')
        data['effort_date'].append(start_date)
        end_date = project.target_date.strftime('%Y-%m-%d (%a)')
        start_staff_days = project.allocated_staff_days
        burndown_line_x = [start_date, end_date]
        burndown_line_y = [start_staff_days, 0]
        burndown_line = [burndown_line_x, burndown_line_y]

    status_entries, all_dates = get_project_weekly_effort(project, current_date)

    assignees = set()
    for entry in status_entries:
        effort_date = entry['effort_date'].strftime('%Y-%m-%d (%a)')
        assignee = entry['task__assignee__github_login']
        estimate_days = entry['estimate_days_sum']
        if effort_date not in data['effort_date']:
            data['effort_date'].append(effort_date)

        effort_date_index = data['effort_date'].index(effort_date)
        while len(data[assignee]) != effort_date_index:
            data[assignee].append(0.0)  # backfill
        data[assignee].append(estimate_days)
        assignees.add(assignee)

    # get max date
    max_date_str = max(data['effort_date'])
    all_date_strings = [d.strftime('%Y-%m-%d (%a)') for d in all_dates]
    unadded_dates = all_date_strings[all_date_strings.index(max_date_str) + 1:]
    for date_str in unadded_dates:
        print(date_str)
        data['effort_date'].append(date_str)
        for assignee in assignees:
            data[assignee].append(0.0)
    return data, sorted(list(assignees)), burndown_line


def updated_hours_worked(projects: KippoProject, start_date: datetime.date = None):
    """

    :param statuses:
    :return:
    """


def get_projects_load(organization: KippoOrganization, schedule_start_date: datetime.date = None) -> Tuple[Dict[Any, List[KippoTask]], datetime.date]:
    """
    Schedule tasks to determine developer work load for projects with is_closed=False belonging to the given organization.

    :param organization: Organization to filter projects by
    :param schedule_start_date: If given, the schedule will be calculated from this date (Otherwise the current date will be used)

    :return:

        .. code::python

            (
                { 'PROJECT_ID':  # multiple
                    {
                        'GITHUB_LOGIN': [  # multiple
                            KippoTask(),
                            KippoTask()
                        ]
                    },
                },
                datetime.date(2018, 9, 21)  # Latest available Effort Date (latest_taskstatus_effort_date) from which the schedule is calculated
            )

    """
    if not schedule_start_date:
        schedule_start_date = timezone.now().date()
    elif isinstance(schedule_start_date, datetime.datetime):
        schedule_start_date = schedule_start_date.date()

    projects = list(KippoProject.objects.filter(target_date__gt=schedule_start_date,
                                                organization=organization,
                                                start_date__isnull=False,
                                                target_date__isnull=False,
                                                is_closed=False))
    if not projects:
        raise ProjectConfigurationError('Project(s) Not properly configured! (Check that 1 or more project has a start_date and target_date defined)')

    # prepare absolute priority
    project_active_state_priority = {
        p.id: {v: k for k, v in p.columnset.get_active_column_names(with_priority=True)}
        for p in projects
    }

    kippo_tasks = {}

    # get the latest available date for KippoTaskStatus effort_date records for the specific organization
    latest_taskstatus_effort_date = KippoTaskStatus.objects.filter(
        task__project__organization=organization
    ).latest('effort_date').effort_date

    if latest_taskstatus_effort_date < schedule_start_date:
        logger.warning(f'Available latest KippoTaskStatus.effort_date < schedule_start_date: {latest_taskstatus_effort_date} < {schedule_start_date}')

    # get related projects and tasks
    qlu_tasks = []
    qlu_milestones = []
    default_minimum = 1
    default_suggested = 3
    maximum_multiplier = 1.7
    for project in projects:
        # NOTE: Should this be filtered by effort_date?
        # -- 'active task status'
        active_taskstatus = KippoTaskStatus.objects.filter(task__project=project,
                                                           task__assignee__is_developer=True,
                                                           task__assignee__is_active=True,
                                                           task__is_closed=False,
                                                           task__assignee__github_login__isnull=False,
                                                           effort_date=latest_taskstatus_effort_date,
                                                           state__in=project.get_active_column_names())
        for status in active_taskstatus:
            if status.state not in project.get_active_column_names():  # this shouldn't be needed as it's being filtered above
                logger.error('state_in filter not working!')
                continue  # Skip non-active states

            # create qlu estimates and tasks
            # - create estimates for task
            minimum_estimate = int(status.minimum_estimate_days) if status.minimum_estimate_days else default_minimum
            suggested_estimate = int(status.estimate_days) if status.estimate_days else default_suggested
            maximum_estimate = status.maximum_estimate_days
            if not maximum_estimate:
                maximum_estimate = int(round(suggested_estimate * maximum_multiplier, 0))
            qestimates = QluTaskEstimates(minimum_estimate,
                                          suggested_estimate,
                                          maximum_estimate)

            # QluTask Fields: (id: Any, absolute_priority, depends_on, estimates, assignee, project_id, milestone_id)
            related_milestone = status.task.milestone
            if related_milestone:
                if not all((related_milestone.start_date, related_milestone.target_date)):
                    raise ValueError(f'"start_date" and "target_date" KippoMilestone({related_milestone.name}): '
                                     f'start_date={related_milestone.start_date}, target_date={related_milestone.target_date}')
                milestone_id = related_milestone.id
                qlu_milestone = QluMilestone(milestone_id,
                                             related_milestone.start_date,
                                             related_milestone.target_date)
            else:
                # treat the parent project as a milestone to get the task start/end
                if not all((project.start_date, project.target_date)):
                    raise ValueError(f'"start_date" and "target_date" Project({project.name}): '
                                     f'start_date={project.start_date}, target_date={project.target_date}')
                milestone_id = f'p-{status.task.project.id}'  # matches below in milestone creation
                qlu_milestone = QluMilestone(milestone_id,
                                             status.task.project.start_date,
                                             status.task.project.target_date)
            qlu_milestones.append(qlu_milestone)

            # pick priority
            state_priority_index = project_active_state_priority[status.task.project.id][status.state]
            priority_offset = 10 * state_priority_index
            task_absolute_priority = status.state_priority + priority_offset  # ok to overlap priorities for now

            kippo_tasks[status.task.id] = status.task
            qtask = QluTask(
                status.task.id,
                absolute_priority=task_absolute_priority,
                estimates=qestimates,
                assignee=status.task.assignee.github_login,
                project_id=project.id,
                milestone_id=milestone_id,
            )
            qlu_tasks.append(qtask)

    project_developer_load = {}
    if not qlu_tasks:
        raise ValueError('No "qlu_tasks" defined!')

    # prepare developer holidays
    holidays = {d.github_login: d.personal_holiday_dates() for d in KippoUser.objects.filter(is_developer=True,
                                                                                             is_active=True)}
    scheduler = QluTaskScheduler(milestones=qlu_milestones,
                                 holiday_calendar=None,  # TODO: Update with proper holiday calendar!
                                 assignee_personal_holidays=holidays,
                                 start_date=schedule_start_date)
    scheduled_results = scheduler.schedule(qlu_tasks)

    for qlu_task in scheduled_results.tasks():
        kippo_task_id = qlu_task.id
        kippo_task = kippo_tasks[kippo_task_id]
        # attach qlu_task to kippo task
        # the qlu_task has the following attributes:
        # -- qlu_task.start_date
        # -- qlu_task.end_date
        # -- qlu_task.is_scheduled
        kippo_task.qlu_task = qlu_task
        project_id = kippo_task.project.id
        if project_id not in project_developer_load:
            project_developer_load[project_id] = defaultdict(list)
        project_developer_load[project_id][kippo_task.assignee.github_login].append(kippo_task)
    return project_developer_load, latest_taskstatus_effort_date


def prepare_project_engineering_load_plot_data(organization: KippoOrganization, assignee_filter: str=None, schedule_start_date: datetime.date=None):
    logger.debug(f'organization: {organization}')
    projects_results, latest_effort_date = get_projects_load(organization, schedule_start_date)
    if not projects_results:
        raise ValueError('(get_projects_load) project_results is empty!')

    project_data = {}
    # prepare data for plotting
    for project_id in projects_results:
        data = {
            'project_ids': [],
            'project_names': [],
            'project_target_dates': [],
            'assignees': [],
            'project_assignee_grouped': [],
            'task_ids': [],
            'task_titles': [],
            'task_estimate_days': [],
            'task_start_dates': [],
            'task_end_dates': [],
        }
        for assignee in projects_results[project_id]:
            if assignee_filter and assignee not in assignee_filter:
                logger.debug(f'assignee_filter({assignee_filter}) applied, skipping: {assignee}')
                continue
            for task in projects_results[project_id][assignee]:
                data['project_ids'].append(project_id)
                data['project_names'].append(task.project.name)
                data['project_target_dates'].append(task.project.target_date)
                data['assignees'].append(assignee)
                data['project_assignee_grouped'].append((task.project.name, assignee))
                data['task_ids'].append(task.id)
                data['task_titles'].append(task.title)
                estimate = task.qlu_task.end_date - task.qlu_task.start_date
                data['task_estimate_days'].append(estimate.days)
                data['task_start_dates'].append(task.qlu_task.start_date)
                data['task_end_dates'].append(task.qlu_task.end_date)
        project_data[project_id] = data

    # prepare project milestone info
    project_milestones = defaultdict(list)
    project_ids = projects_results.keys()
    for milestone in KippoMilestone.objects.filter(project__id__in=project_ids).order_by('target_date'):
        milestone_info = {
            'project_id': milestone.project.id,
            'start_date': milestone.start_date,
            'target_date': milestone.target_date,
            'title': milestone.title,
            'description': milestone.description,
        }
        project_milestones[milestone.project.id].append(milestone_info)

    script, div = prepare_project_schedule_chart_components(project_data, project_milestones)
    return (script, div), latest_effort_date
