import datetime
import logging
from math import ceil
from itertools import islice
from collections import defaultdict
from typing import Dict, List, Any, Tuple, Generator

from django.conf import settings
from django.utils import timezone

from ghorgs.wrappers import GithubIssue
from qlu.core import QluTaskScheduler, QluTask, QluMilestone, QluTaskEstimates

from accounts.models import KippoOrganization, OrganizationMembership
from projects.models import KippoProject, KippoMilestone
from .exceptions import ProjectConfigurationError, OrganizationKippoTaskStatusError
from .models import KippoTask, KippoTaskStatus
from .charts.functions import prepare_project_schedule_chart_components


logger = logging.getLogger(__name__)
TUESDAY_WEEKDAY = 2
DEFAULT_HOURSWORKED_DATERANGE = timezone.timedelta(days=7)


class GithubIssuePrefixedLabel:

    def __init__(self, label: object, prefix_delim: str = ':'):
        self.label = label
        self.prefix_delim = prefix_delim

        # https://developer.github.com/v3/issues/labels/#get-a-single-label
        label_attributes = (
            'id',
            'node_id',
            'url',
            'name',
            'color',
            'default'
        )
        for attrname in label_attributes:
            attrvalue = getattr(label, attrname)
            setattr(self, attrname, attrvalue)

    @property
    def prefix(self):
        return self.name.split(self.prefix_delim)[0]

    @property
    def value(self):
        return self.name.split(self.prefix_delim)[-1]


def get_github_issue_prefixed_labels(issue: GithubIssue, prefix_delim: str = ':') -> List[GithubIssuePrefixedLabel]:
    """Process a label in the format of a prefix/value"""
    prefixed_labels = []
    for label in issue.labels:
        prefixed_label = GithubIssuePrefixedLabel(label, prefix_delim=prefix_delim)
        prefixed_labels.append(prefixed_label)
    return prefixed_labels


def get_github_issue_estimate_label(
        issue: GithubIssue,
        prefix: str = settings.DEFAULT_GITHUB_ISSUE_LABEL_ESTIMATE_PREFIX,
        day_workhours: int = settings.DAY_WORKHOURS) -> int:
    """
    Parse the estimate label into an estimate value
    Estimate labels follow the scheme: {prefix}N{suffix}
    WHERE:
    - {prefix} estimate label identifier
    - N is a positive integer representing number of days
    - {suffix} one of ('d', 'day', 'days', 'h', 'hour', 'hours')
    - If multiple estimate labels are defined the larger value will be used
    - If no suffix is given, 'days' will be assumed

    .. note::

        Only integer values are supported.
        (fractional days are not represented at the moment)


    :param issue: github issue object
    :param prefix: This identifies the github issue label as being an
    :param day_workhours: Number of hours in the workday
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

            candidate_estimate = int(estimate_str_value)

            if label.name.endswith(('h', 'hour', 'hours')):
                # all estimates are normalized to days
                # if hours convert to a days
                candidate_estimate = int(ceil(candidate_estimate / day_workhours))

            if estimate and candidate_estimate:
                if candidate_estimate > estimate:
                    logger.warning(f'multiple estimate labels found for issue({issue}), using the larger value: {estimate} -> {candidate_estimate}')
                    estimate = candidate_estimate
            else:
                estimate = candidate_estimate

    return estimate


def get_github_issue_category_label(issue: GithubIssue, prefix=settings.DEFAULT_GITHUB_ISSUE_LABEL_CATEGORY_PREFIX) -> str:
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
            if category:
                raise ValueError(f'Multiple Category labels applied on issue: {issue.html_url}')
            category = label.name.split(prefix)[-1].strip()
    return category


class ProjectDatesError(Exception):
    pass


class TaskStatusError(Exception):
    pass


def window(seq, n=2) -> Generator:
    """
    Returns a sliding window (of width n) over data from the iterable
    s -> (s0,s1,...s[n-1]), (s1,s2,...,sn), ...
    """
    it = iter(seq)
    result = tuple(islice(it, n))
    if len(result) == n:
        yield result
    for elem in it:
        result = result[1:] + (elem,)
        yield result


def update_kippotaskstatus_hours_worked(projects: KippoProject,
                                        start_date: datetime.date = None,
                                        date_delta: timezone.timedelta=DEFAULT_HOURSWORKED_DATERANGE) -> List[KippoTaskStatus]:
    """
    Obtain the calculated hours_worked between KippoTaskStatus objects for the same task from different effort_date(s)
    return all calculations for

    :param projects: Projects to update
    :param start_date:
    :param date_delta: How many days back to include in search
    :return: Task Statuses objects that were updated
    """
    period_start_date = start_date - date_delta
    projects_map = {p.id: p for p in projects}
    # get KippoTaskStatus for KippoProjects given which are not yet updated
    statuses = KippoTaskStatus.objects.filter(task__project__in=projects,
                                              effort_date__gte=period_start_date).order_by('task', 'effort_date')

    task_taskstatuses = defaultdict(list)
    for status in statuses:
        task_taskstatuses[status.task.id].append(status)  # expect to be in order

    updated_statuses = []
    for task_id, task_statuses in task_taskstatuses.items():
        for earlier_status, later_status in window(task_statuses, n=2):
            if earlier_status.estimate_days and later_status.estimate_days:
                if later_status.hours_spent is None:
                    # update
                    change_in_days = earlier_status.estimate_days - later_status.estimate_days
                    logger.debug(f'change_in_days: {change_in_days}')
                    if change_in_days >= 0:  # ignore increases in estimates
                        # calculate based on project work days
                        project = projects_map[later_status.task.project.id]
                        day_workhours = project.organization.day_workhours
                        calculated_work_hours = change_in_days * day_workhours
                        later_status.hours_spent = calculated_work_hours
                        later_status.save()
                        updated_statuses.append(later_status)
                        logger.info(f'({later_status.task.title} [{later_status.effort_date}]) '
                                    f'Updated KippoTaskStatus.hours_spent={calculated_work_hours}')
                    else:
                        logger.warning(f'Estimate increased, KippoTaskStatus NOT updated: '
                                       f'{earlier_status.estimate_days} - {later_status.estimate_days} = {change_in_days}')
    return updated_statuses


def _get_latest_kippotaskstatus_effortdate(organization: KippoOrganization) -> timezone.datetime.date:
    """get the latest available date for KippoTaskStatus effort_date records for the specific organization"""
    logger.debug(f'Collecting KippoTaskStatus for organization: {organization}')
    try:
        latest_taskstatus_effort_date = KippoTaskStatus.objects.filter(
            task__project__organization=organization
        ).latest('effort_date').effort_date
    except KippoTaskStatus.DoesNotExist as e:
        logger.exception(e)
        msg = f'No KippoTaskStatus entries for Organization: {organization}'
        logger.error(msg)
        raise OrganizationKippoTaskStatusError(msg)
    return latest_taskstatus_effort_date


def get_projects_load(organization: KippoOrganization, schedule_start_date: datetime.date = None) -> Tuple[Dict[Any, Dict[str, List[KippoTask]]], datetime.date]:
    """
    Schedule tasks to determine developer work load for projects with is_closed=False belonging to the given organization.
    Returned KippoTasks are augmented with the scheduled resulting QluTask object as the 'qlu_task' attribute.

    :param organization: Organization to filter projects by
    :param schedule_start_date: If given, the schedule will be calculated from this date (Otherwise the current date will be used)

    :return:

        .. code::python

            (
                { 'PROJECT_ID':  # multiple
                    {
                        'GITHUB_LOGIN': [  # multiple
                            KippoTask(),  # with 'qlu_task' attribute with scheduled QluTask object
                            KippoTask()   # with 'qlu_task' attribute with scheduled QluTask object
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

    projects = list(KippoProject.objects.filter(
        organization=organization,
        start_date__isnull=False,
        target_date__isnull=False,
        is_closed=False).order_by('target_date')
    )
    if not projects:
        raise ProjectConfigurationError('No projects found! Project(s) Not properly configured! (Check that 1 or more project has a start_date and target_date defined)')

    # prepare absolute priority
    project_active_state_priority = {
        p.id: {v: k for k, v in p.columnset.get_active_column_names(with_priority=True)}
        for p in projects
    }

    kippo_tasks = {}

    # get the latest available date for KippoTaskStatus effort_date records for the specific organization
    latest_taskstatus_effort_date = _get_latest_kippotaskstatus_effortdate(organization)
    logger.debug(f'Collecting KippoTaskStatus for organization: {organization}')

    if latest_taskstatus_effort_date < schedule_start_date:
        logger.warning(f'Available latest KippoTaskStatus.effort_date < schedule_start_date: {latest_taskstatus_effort_date} < {schedule_start_date}')

    # get related projects and tasks
    qlu_tasks = []
    qlu_milestones = []
    default_minimum = 1
    default_suggested = 3
    maximum_multiplier = 1.7
    organization_developers = list(organization.get_github_developer_kippousers())
    for project in projects:
        # NOTE: Should this be filtered by effort_date?
        # -- 'active task status'
        active_taskstatus = KippoTaskStatus.objects.filter(
            task__project=project,
            task__assignee__in=organization_developers,
            task__is_closed=False,
            effort_date=latest_taskstatus_effort_date,
            state__in=project.get_active_column_names()
        )
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

            # TODO: review milestone handling
            # QluTask Fields: (id: Any, absolute_priority, depends_on, estimates, assignee, project_id, milestone_id)
            related_milestone = status.task.milestone
            if related_milestone:
                if not all((related_milestone.start_date, related_milestone.target_date)):
                    raise ValueError(f'"start_date" and "target_date" KippoMilestone({related_milestone.name}): '
                                     f'start_date={related_milestone.start_date}, target_date={related_milestone.target_date}')
                milestone_id = related_milestone.id
                logger.debug(
                    f'Using KippoMilestone({related_milestone.title}) as QluMilestone({milestone_id}): '
                    f'start_date={related_milestone.start_date}, target_date={related_milestone.target_date}'
                )

                qlu_milestone = QluMilestone(milestone_id,
                                             related_milestone.start_date,
                                             related_milestone.target_date)
            else:
                # treat the parent project as a milestone to get the task start/end
                if not all((project.start_date, project.target_date)):
                    raise ValueError(f'"start_date" and "target_date" Project({project.name}): '
                                     f'start_date={project.start_date}, target_date={project.target_date}')
                milestone_id = f'p-{status.task.project.id}'  # matches below in milestone creation
                logger.debug(
                    f'Using KippoProject({project.name}) as QluMilestone({milestone_id}): '
                    f'start_date={project.start_date}, target_date={project.target_date}'
                )

                qlu_milestone = QluMilestone(milestone_id,
                                             status.task.project.start_date,
                                             status.task.project.target_date)
            qlu_milestones.append(qlu_milestone)

            # pick priority
            state_priority_index = project_active_state_priority[status.task.project.id][status.state]
            priority_offset = 10 * state_priority_index
            task_absolute_priority = status.state_priority + priority_offset  # ok to overlap priorities for now

            logger.debug(
                f'QluTask.id={status.task.id}:{status.task.github_issue_html_url}'
            )
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

    # prepare developer workdays and developer personal holidays
    workdays = {}
    holidays = {}
    for kippo_org_developer in organization_developers:
        organization_membership = OrganizationMembership.objects.get(
            user=kippo_org_developer,
            organization=organization
        )

        workdays[kippo_org_developer.github_login] = organization_membership.get_workday_identifers()
        holidays[kippo_org_developer.github_login] = list(kippo_org_developer.personal_holiday_dates())
        holidays[kippo_org_developer.github_login].extend(list(kippo_org_developer.public_holiday_dates()))

    scheduler = QluTaskScheduler(
        milestones=qlu_milestones,
        holiday_calendar=None,  # TODO: Update with proper holiday calendar!
        assignee_workdays=workdays,
        assignee_personal_holidays=holidays,
        start_date=schedule_start_date
    )

    # TODO: fails on calc....
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


def prepare_project_engineering_load_plot_data(organization: KippoOrganization, assignee_filter: str = None, schedule_start_date: datetime.date = None):
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
            'project_start_dates': [],
            'project_target_dates': [],
            'assignees': [],
            'project_assignee_grouped': [],
            'task_ids': [],
            'task_urls': [],
            'task_titles': [],
            'assignee_calendar_days': [],
            'task_estimate_days': [],
            'task_start_dates': [],
            'task_end_dates': [],
        }
        project_populated = False
        for assignee in projects_results[project_id]:
            if assignee_filter and assignee not in assignee_filter:
                logger.debug(f'assignee_filter({assignee_filter}) applied, skipping: {assignee}')
                continue
            for task in projects_results[project_id][assignee]:
                latest_kippotaskstatus = task.latest_kippotaskstatus()
                data['project_ids'].append(str(project_id))
                data['project_names'].append(task.project.name)
                data['project_start_dates'].append(task.project.start_date)  # only 1 is really needed...
                data['project_target_dates'].append(task.project.target_date)  # only 1 is really needed...
                data['assignees'].append(assignee)
                data['project_assignee_grouped'].append((task.project.name, assignee))
                data['task_ids'].append(task.id)
                data['task_urls'].append(task.github_issue_html_url)
                data['task_titles'].append(task.title)
                estimate = task.qlu_task.end_date - task.qlu_task.start_date
                data['assignee_calendar_days'].append(estimate.days)
                data['task_estimate_days'].append(latest_kippotaskstatus.estimate_days)
                data['task_start_dates'].append(task.qlu_task.start_date)
                data['task_end_dates'].append(task.qlu_task.end_date)
                project_populated = True
        if project_populated:  # may not be filled if using assignee filter
            project_data[project_id] = data
        else:
            logger.warning(f'No data for Project-id({project_id}): {assignee_filter}')

    # prepare project milestone info
    project_milestones = defaultdict(list)
    project_ids = projects_results.keys()
    for milestone in KippoMilestone.objects.filter(project__id__in=project_ids).order_by('target_date'):
        milestone_info = {
            'project_id': str(milestone.project.id),
            'start_date': milestone.start_date,
            'target_date': milestone.target_date,
            'title': milestone.title,
            'description': milestone.description,
        }
        project_milestones[milestone.project.id].append(milestone_info)

    script, div = prepare_project_schedule_chart_components(project_data, project_milestones)
    return (script, div), latest_effort_date
