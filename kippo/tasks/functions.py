import datetime
import logging
from collections import defaultdict
from itertools import islice
from math import ceil
from typing import Any, Dict, Generator, List, Optional, Tuple

from accounts.models import Country, KippoOrganization, KippoUser, OrganizationMembership, PublicHoliday
from django.conf import settings
from django.utils import timezone
from ghorgs.wrappers import GithubIssue
from projects.models import KippoMilestone, KippoProject
from qlu.core import QluMilestone, QluTask, QluTaskEstimates, QluTaskScheduler

from .charts.functions import prepare_project_schedule_chart_components
from .exceptions import OrganizationKippoTaskStatusError, ProjectConfigurationError
from .models import KippoTask, KippoTaskStatus

logger = logging.getLogger(__name__)
TUESDAY_WEEKDAY = 2
WEEKENDS = (5, 6)
DEFAULT_HOURSWORKED_DATERANGE = timezone.timedelta(days=7)
DATE_DISPLAY_FORMAT = "%Y-%m-%d (%a)"


class GithubIssuePrefixedLabel:
    def __init__(self, label: object, prefix_delim: str = ":"):
        self.label = label
        self.prefix_delim = prefix_delim

        # https://developer.github.com/v3/issues/labels/#get-a-single-label
        label_attributes = ("id", "node_id", "url", "name", "color", "default")
        for attrname in label_attributes:
            attrvalue = getattr(label, attrname)
            setattr(self, attrname, attrvalue)

    @property
    def prefix(self):
        return self.name.split(self.prefix_delim)[0]

    @property
    def value(self):
        return self.name.split(self.prefix_delim)[-1]


def get_github_issue_prefixed_labels(issue: GithubIssue, prefix_delim: str = ":") -> List[GithubIssuePrefixedLabel]:
    """Process a label in the format of a prefix/value"""
    prefixed_labels = []
    for label in issue.labels:
        prefixed_label = GithubIssuePrefixedLabel(label, prefix_delim=prefix_delim)
        prefixed_labels.append(prefixed_label)
    return prefixed_labels


def build_latest_comment(issue: GithubIssue) -> str:
    latest_comment = ""
    if issue.latest_comment_body:
        latest_comment = f"{issue.latest_comment_created_by} [ {issue.latest_comment_created_at} ] " f"{issue.latest_comment_body}"
    return latest_comment


def get_tags_from_prefixedlabels(prefixed_labels: List[GithubIssuePrefixedLabel]) -> List[Dict[str, str]]:
    tags = []
    for prefixed_label in prefixed_labels:
        # more than 1 label with the same prefix may exist
        tags.append({"name": prefixed_label.prefix, "value": prefixed_label.value})
    return tags


def get_github_issue_estimate_label(
    issue: GithubIssue, prefix: str = settings.DEFAULT_GITHUB_ISSUE_LABEL_ESTIMATE_PREFIX, day_workhours: int = settings.DAY_WORKHOURS
) -> int:
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
    valid_label_suffixes = ("d", "day", "days", "h", "hour", "hours")
    for label in issue.labels:
        if label.name.startswith(prefix):
            estimate_str_value = label.name.split(prefix)[-1]
            for suffix in valid_label_suffixes:
                if estimate_str_value.endswith(suffix):  # d = days, h = hours
                    estimate_str_value = estimate_str_value.split(suffix)[0]

            try:
                candidate_estimate = int(estimate_str_value)
            except ValueError:
                logger.error(f"Invalid estimate value cannot convert to int() estimate_str_value={estimate_str_value}, label.name={label.name}")

            if candidate_estimate:
                if label.name.endswith(("h", "hour", "hours")):
                    # all estimates are normalized to days
                    # if hours convert to a days
                    candidate_estimate = int(ceil(candidate_estimate / day_workhours))

                if estimate and candidate_estimate:
                    if candidate_estimate > estimate:
                        logger.warning(
                            f"multiple estimate labels found for issue({issue}), using the larger value: {estimate} -> {candidate_estimate}"
                        )
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
                raise ValueError(f"Multiple Category labels applied on issue: {issue.html_url}")
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


def update_kippotaskstatus_hours_worked(
    projects: KippoProject, start_date: datetime.date = None, date_delta: timezone.timedelta = DEFAULT_HOURSWORKED_DATERANGE
) -> List[KippoTaskStatus]:
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
    statuses = KippoTaskStatus.objects.filter(task__project__in=projects, effort_date__gte=period_start_date).order_by("task", "effort_date")

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
                    logger.debug(f"change_in_days: {change_in_days}")
                    if change_in_days >= 0:  # ignore increases in estimates
                        # calculate based on project work days
                        project = projects_map[later_status.task.project.id]
                        day_workhours = project.organization.day_workhours
                        calculated_work_hours = change_in_days * day_workhours
                        later_status.hours_spent = calculated_work_hours
                        later_status.save()
                        updated_statuses.append(later_status)
                        logger.info(
                            f"({later_status.task.title} [{later_status.effort_date}]) "
                            f"Updated KippoTaskStatus.hours_spent={calculated_work_hours}"
                        )
                    else:
                        logger.warning(
                            f"Estimate increased, KippoTaskStatus NOT updated: "
                            f"{earlier_status.estimate_days} - {later_status.estimate_days} = {change_in_days}"
                        )
    return updated_statuses


def _get_latest_kippotaskstatus_effortdate(organization: KippoOrganization) -> timezone.datetime.date:
    """get the latest available date for KippoTaskStatus effort_date records for the specific organization"""
    logger.debug(f"Collecting KippoTaskStatus for organization: {organization}")
    try:
        latest_taskstatus_effort_date = KippoTaskStatus.objects.filter(task__project__organization=organization).latest("effort_date").effort_date
    except KippoTaskStatus.DoesNotExist as e:
        logger.exception(e)
        msg = f"No KippoTaskStatus entries for Organization: {organization}"
        logger.error(msg)
        raise OrganizationKippoTaskStatusError(msg)
    return latest_taskstatus_effort_date


def get_projects_load(
    organization: KippoOrganization, schedule_start_date: datetime.date = None
) -> Tuple[Dict[Any, Dict[str, List[KippoTask]]], Dict[str, Dict[datetime.date, str]], datetime.date]:
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

    projects = list(
        KippoProject.objects.filter(organization=organization, start_date__isnull=False, target_date__isnull=False, is_closed=False).order_by(
            "target_date"
        )
    )
    if not projects:
        raise ProjectConfigurationError(
            "No projects found! Project(s) Not properly configured! (Check that 1 or more project has a start_date and target_date defined)"
        )

    # prepare absolute priority
    project_active_state_priority = {p.id: {v: k for k, v in p.columnset.get_active_column_names(with_priority=True)} for p in projects}

    kippo_tasks = {}

    # get the latest available date for KippoTaskStatus effort_date records for the specific organization
    latest_taskstatus_effort_date = _get_latest_kippotaskstatus_effortdate(organization)
    logger.debug(f"Collecting KippoTaskStatus for organization: {organization}")

    if latest_taskstatus_effort_date < schedule_start_date:
        logger.warning(f"Available latest KippoTaskStatus.effort_date < schedule_start_date: {latest_taskstatus_effort_date} < {schedule_start_date}")

    # get related projects and tasks
    qlu_tasks = []
    qlu_milestones = []
    default_minimum = 1
    default_suggested = 3
    maximum_multiplier = 1.7
    organization_developers = list(organization.get_github_developer_kippousers())
    additional_filters = {"task__is_closed": False, "task__assignee__in": organization_developers}
    for project in projects:
        # -- 'active task status'
        active_taskstatus, _ = project.get_active_taskstatus(additional_filters=additional_filters)
        logger.debug(f"{project} len(active_taskstatus)={len(active_taskstatus)}")
        for status in active_taskstatus:
            # create qlu estimates and tasks
            # - create estimates for task
            minimum_estimate = int(status.minimum_estimate_days) if status.minimum_estimate_days else default_minimum
            suggested_estimate = int(status.estimate_days) if status.estimate_days else default_suggested
            maximum_estimate = status.maximum_estimate_days
            if not maximum_estimate:
                maximum_estimate = int(round(suggested_estimate * maximum_multiplier, 0))
            qestimates = QluTaskEstimates(minimum_estimate, suggested_estimate, maximum_estimate)

            # TODO: review milestone handling
            # QluTask Fields: (id: Any, absolute_priority, depends_on, estimates, assignee, project_id, milestone_id)
            related_milestone = status.task.milestone
            if related_milestone:
                if not all((related_milestone.start_date, related_milestone.target_date)):
                    raise ValueError(
                        f'"start_date" and "target_date" KippoMilestone({related_milestone.title}): '
                        f"start_date={related_milestone.start_date}, target_date={related_milestone.target_date}"
                    )
                milestone_id = related_milestone.id
                logger.debug(
                    f"Using KippoMilestone({related_milestone.title}) as QluMilestone({milestone_id}): "
                    f"start_date={related_milestone.start_date}, target_date={related_milestone.target_date}"
                )

                qlu_milestone = QluMilestone(milestone_id, related_milestone.start_date, related_milestone.target_date)
            else:
                # treat the parent project as a milestone to get the task start/end
                if not all((project.start_date, project.target_date)):
                    raise ValueError(
                        f'"start_date" and "target_date" Project({project.name}): '
                        f"start_date={project.start_date}, target_date={project.target_date}"
                    )
                milestone_id = f"p-{status.task.project.id}"  # matches below in milestone creation
                logger.debug(
                    f"Using KippoProject({project.name}) as QluMilestone({milestone_id}): "
                    f"start_date={project.start_date}, target_date={project.target_date}"
                )

                qlu_milestone = QluMilestone(milestone_id, status.task.project.start_date, status.task.project.target_date)
            qlu_milestones.append(qlu_milestone)

            # pick priority
            state_priority_index = project_active_state_priority[status.task.project.id][status.state]
            priority_offset = 10 * state_priority_index
            if status.state_priority:
                task_absolute_priority = status.state_priority + priority_offset  # ok to overlap priorities for now
            else:
                task_absolute_priority = len(active_taskstatus) + priority_offset

            logger.debug(f"QluTask.id={status.task.id}:{status.task.github_issue_html_url}")
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
        organization_membership = OrganizationMembership.objects.get(user=kippo_org_developer, organization=organization)

        workdays[kippo_org_developer.github_login] = organization_membership.get_workday_identifers()
        holidays[kippo_org_developer.github_login] = list(kippo_org_developer.personal_holiday_dates())
        holidays[kippo_org_developer.github_login].extend(list(kippo_org_developer.public_holiday_dates()))

    scheduler = QluTaskScheduler(
        milestones=qlu_milestones,
        holiday_calendar=None,  # Currently Holidays are included in the 'holidays' variable this is not needed
        assignee_workdays=workdays,
        assignee_personal_holidays=holidays,
        start_date=schedule_start_date,
    )
    scheduled_results = scheduler.schedule(qlu_tasks)
    assignee_date_keyed_scheduled_projects_ids = defaultdict(dict)
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
        project_name = kippo_task.project.name
        if project_id not in project_developer_load:
            project_developer_load[project_id] = defaultdict(list)
        project_developer_load[project_id][kippo_task.assignee.github_login].append(kippo_task)
        for task_date in qlu_task.scheduled_dates:
            assignee_date_keyed_scheduled_projects_ids[kippo_task.assignee.github_login][task_date] = str(project_name)
    return project_developer_load, assignee_date_keyed_scheduled_projects_ids, latest_taskstatus_effort_date


def _add_assignee_project_data(
    organization: KippoOrganization,
    schedule_start_date: datetime.date,
    assignee_github_login: str,
    assignee_tasks: list,
    country_holidays: Dict[Country, List[PublicHoliday]],
    assignee_date_keyed_scheduled_projects_ids: Dict[str, Dict[datetime.date, str]],
    max_days: int = 65,
) -> Tuple[Dict[str, list], datetime.date, datetime.date, int, datetime.date, bool]:
    assignee_data = {
        # length of columns expected to be the same
        "project_ids": [],
        "project_names": [],
        "current_dates": [],
        "assignee_calendar_days": [],
        "assignees": [],
        "project_assignee_grouped": [],
        "task_ids": [],
        "task_urls": [],
        "task_titles": [],
        "task_estimate_days": [],
        "task_dates": [],
        "descriptions": [],
        "holiday_dates": [],
        "weekend_dates": [],
        "scheduled_dates": [],
        "unscheduled_dates": [],
        "uncommitted_dates": [],
        "personal_holiday_dates": [],
    }
    assignee_kippouser = KippoUser.objects.get(github_login=assignee_github_login)
    organization_membership = assignee_kippouser.get_membership(organization)
    logger.info(f"assignee_github_login organization_membership.committed_weekdays={organization_membership.committed_weekdays}")
    personal_holiday_dates = list(assignee_kippouser.personal_holiday_dates())
    assignee_public_holidays = country_holidays.get(assignee_kippouser.holiday_country, None)
    date_keyed_holidays = {}
    if assignee_public_holidays:
        date_keyed_holidays = {h.day: h for h in assignee_public_holidays}

    assignee_scheduled_dates = []
    assignee_total_scheduled_days = 0
    project_populated = False
    project_id = None
    project_name = None
    project_start_date = None
    project_target_date = None
    project_assignee_group = None
    assignee_max_task_date = None
    for task in assignee_tasks:
        latest_kippotaskstatus = task.latest_kippotaskstatus()
        required_calendar_days = task.qlu_task.end_date - task.qlu_task.start_date
        project_id = str(task.project.id)
        project_name = task.project.name
        project_start_date = task.project.start_date
        project_target_date = task.project.target_date
        project_assignee_group = (task.project.name, assignee_kippouser.display_name)
        if latest_kippotaskstatus and latest_kippotaskstatus.estimate_days:
            assignee_total_scheduled_days += latest_kippotaskstatus.estimate_days

        for task_date in task.qlu_task.scheduled_dates:
            if not assignee_max_task_date:
                assignee_max_task_date = task_date
            elif assignee_max_task_date and assignee_max_task_date < task_date:
                assignee_max_task_date = task_date
            logger.debug(
                f"assignee_github_login={assignee_github_login}, assignee_max_task_date={assignee_max_task_date}, project_name={project_name}, "
                f"task.title={task.title} ({latest_kippotaskstatus.estimate_days}) {task.github_issue_html_url}"
            )
            assignee_data["project_ids"].append(project_id)
            assignee_data["project_names"].append(project_name)
            assignee_data["assignees"].append(assignee_github_login)
            assignee_data["project_assignee_grouped"].append(project_assignee_group)
            assignee_data["current_dates"].append(task_date.strftime(DATE_DISPLAY_FORMAT))
            assignee_data["scheduled_dates"].append(None)
            if task_date.weekday() in WEEKENDS:
                # add weekend dates
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append("weekend")
                assignee_data["holiday_dates"].append(None)
                assignee_data["weekend_dates"].append(task_date)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(None)
                assignee_data["personal_holiday_dates"].append(None)
            elif task_date in date_keyed_holidays.keys():
                holiday_name = date_keyed_holidays[task_date].name
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append(holiday_name)
                assignee_data["holiday_dates"].append(task_date)
                assignee_data["weekend_dates"].append(None)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(None)
                assignee_data["personal_holiday_dates"].append(None)
            elif task_date in personal_holiday_dates:
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append("peronal holiday")
                assignee_data["holiday_dates"].append(None)
                assignee_data["weekend_dates"].append(None)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(None)
                assignee_data["personal_holiday_dates"].append(task_date)
            elif task_date.weekday() not in organization_membership.committed_weekdays:
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append("uncommitted")
                assignee_data["holiday_dates"].append(None)
                assignee_data["weekend_dates"].append(None)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(task_date)
                assignee_data["personal_holiday_dates"].append(None)
            else:
                assignee_data["task_ids"].append(task.id)
                assignee_data["task_urls"].append(task.github_issue_html_url)
                assignee_data["task_titles"].append(task.title)
                assignee_data["assignee_calendar_days"].append(required_calendar_days)
                assignee_data["task_estimate_days"].append(latest_kippotaskstatus.estimate_days)
                assignee_data["task_dates"].append(task_date)
                assignee_data["descriptions"].append("assigned task")
                assignee_data["holiday_dates"].append(None)
                assignee_data["weekend_dates"].append(None)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(None)
                assignee_data["personal_holiday_dates"].append(None)
            assignee_scheduled_dates.append(task_date)
        project_populated = True

    # fill additional dates for assignee
    current_date = schedule_start_date
    for days in range(max_days):
        current_date += datetime.timedelta(days=1)
        if current_date not in assignee_scheduled_dates:
            assignee_data["project_ids"].append(project_id)
            assignee_data["project_names"].append(project_name)
            assignee_data["assignees"].append(assignee_github_login)
            assignee_data["project_assignee_grouped"].append(project_assignee_group)
            assignee_data["current_dates"].append(current_date.strftime(DATE_DISPLAY_FORMAT))
            scheduled_project_id = assignee_date_keyed_scheduled_projects_ids[assignee_github_login].get(current_date, None)
            if current_date.weekday() in WEEKENDS:
                # add weekend dates
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append("weekend")
                assignee_data["holiday_dates"].append(None)
                assignee_data["weekend_dates"].append(current_date)
                assignee_data["scheduled_dates"].append(None)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(None)
                assignee_data["personal_holiday_dates"].append(None)
            elif current_date in date_keyed_holidays.keys():
                # add holidays
                holiday_name = date_keyed_holidays[current_date].name
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append(holiday_name)
                assignee_data["holiday_dates"].append(current_date)
                assignee_data["weekend_dates"].append(None)
                assignee_data["scheduled_dates"].append(None)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(None)
                assignee_data["personal_holiday_dates"].append(None)
            elif current_date in personal_holiday_dates:
                # add pto dates
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append("personal holiday")
                assignee_data["holiday_dates"].append(None)
                assignee_data["weekend_dates"].append(None)
                assignee_data["scheduled_dates"].append(None)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(None)
                assignee_data["personal_holiday_dates"].append(current_date)
            elif current_date.weekday() not in organization_membership.committed_weekdays:
                # add uncommitted_dates
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append("uncommitted")
                assignee_data["holiday_dates"].append(None)
                assignee_data["weekend_dates"].append(None)
                assignee_data["scheduled_dates"].append(None)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(current_date)
                assignee_data["personal_holiday_dates"].append(None)
            elif scheduled_project_id:
                # add scheduled_dates (other project)
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append(f"scheduled in {scheduled_project_id}")
                assignee_data["holiday_dates"].append(None)
                assignee_data["weekend_dates"].append(None)
                assignee_data["scheduled_dates"].append(current_date)
                assignee_data["unscheduled_dates"].append(None)
                assignee_data["uncommitted_dates"].append(None)
                assignee_data["personal_holiday_dates"].append(None)
            else:
                # add unscheduled_dates
                assignee_data["task_ids"].append(None)
                assignee_data["task_urls"].append(None)
                assignee_data["task_titles"].append(None)
                assignee_data["assignee_calendar_days"].append(None)
                assignee_data["task_estimate_days"].append(None)
                assignee_data["task_dates"].append(None)
                assignee_data["descriptions"].append("unscheduled")
                assignee_data["holiday_dates"].append(None)
                assignee_data["weekend_dates"].append(None)
                assignee_data["scheduled_dates"].append(None)
                assignee_data["unscheduled_dates"].append(current_date)
                assignee_data["uncommitted_dates"].append(None)
                assignee_data["personal_holiday_dates"].append(None)
    return assignee_data, project_start_date, project_target_date, assignee_total_scheduled_days, assignee_max_task_date, project_populated


def prepare_project_engineering_load_plot_data(
    organization: KippoOrganization, assignee_filter: str = None, schedule_start_date: Optional[datetime.date] = None
):
    logger.debug(f"organization: {organization}")
    if not schedule_start_date:
        schedule_start_date = timezone.now().date()
    logger.info(f"schedule_start_date={schedule_start_date}")
    max_days = 70
    projects_results, assignee_date_keyed_scheduled_projects_ids, latest_effort_date = get_projects_load(organization, schedule_start_date)
    if not projects_results:
        raise ValueError("(get_projects_load) project_results is empty!")

    country_holidays = defaultdict(list)
    for public_holiday in PublicHoliday.objects.filter(day__gte=schedule_start_date):
        country_holidays[public_holiday.country].append(public_holiday)

    project_data = []
    # prepare data for plotting
    for project_id in projects_results:
        data = {
            # length of columns expected to be the same
            "project_ids": [],
            "project_names": [],
            "current_dates": [],
            "assignee_calendar_days": [],
            "assignees": [],
            "project_assignee_grouped": [],
            "task_ids": [],
            "task_urls": [],
            "task_titles": [],
            "task_estimate_days": [],
            "task_dates": [],
            "descriptions": [],
            "holiday_dates": [],
            "weekend_dates": [],
            "scheduled_dates": [],
            "unscheduled_dates": [],
            "uncommitted_dates": [],
            "personal_holiday_dates": [],
        }
        project_assignee_data = defaultdict(dict)
        project_populated = False
        project_start_date = None
        project_target_date = None
        project_estimate_date = None
        for assignee, assignee_tasks in projects_results[project_id].items():
            if assignee_filter and assignee not in assignee_filter:
                logger.debug(f"assignee_filter({assignee_filter}) applied, skipping: {assignee}")
                continue
            assignee_data, project_start_date, project_target_date, assignee_total_days, assignee_max_task_date, populated = _add_assignee_project_data(
                organization,
                schedule_start_date,
                assignee,
                assignee_tasks,
                country_holidays,
                assignee_date_keyed_scheduled_projects_ids,
                max_days=max_days,
            )
            if populated:
                project_populated = True
            if not project_estimate_date:
                project_estimate_date = assignee_max_task_date
            elif project_estimate_date and project_estimate_date < assignee_max_task_date:
                project_estimate_date = assignee_max_task_date

            for category, values in assignee_data.items():
                data[category].extend(values)
            project_assignee_data[assignee]["total_scheduled_days"] = assignee_total_days
            project_assignee_data[assignee]["estimated_complete_date"] = assignee_max_task_date

        if project_populated:  # may not be filled if using assignee filter
            project_data.append((project_id, project_start_date, project_target_date, project_estimate_date, data, project_assignee_data))
        else:
            logger.warning(f"No data for Project-id({project_id}): {assignee_filter}")

    # prepare project milestone info
    project_milestones = defaultdict(list)
    project_ids = projects_results.keys()
    for milestone in KippoMilestone.objects.filter(project__id__in=project_ids).order_by("target_date"):
        milestone_info = {
            "project_id": str(milestone.project.id),
            "start_date": milestone.start_date,
            "target_date": milestone.target_date,
            "title": milestone.title,
            "description": milestone.description,
        }
        project_milestones[milestone.project.id].append(milestone_info)

    logger.debug(f"len(project_data)={len(project_data)}")
    logger.debug(f"project_milestones={project_milestones}")

    script, div = prepare_project_schedule_chart_components(project_data, schedule_start_date, project_milestones, display_days=max_days)
    return (script, div), latest_effort_date
