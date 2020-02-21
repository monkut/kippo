"""
For functions used to create project based charts
"""
import datetime
import logging
from typing import Tuple, List
from collections import defaultdict
from math import pi

from bokeh.plotting import figure
from bokeh.resources import CDN
from bokeh.embed import components
from bokeh.core.properties import value
from bokeh.palettes import all_palettes

from django.utils import timezone
from django.db.models import Sum, Value, Count
from django.db.models.functions import Coalesce

from tasks.models import KippoTaskStatus
from ..exceptions import ProjectDatesError, TaskStatusError
from ..models import KippoProject


TUESDAY_WEEKDAY = 2

logger = logging.getLogger(__name__)


def get_project_weekly_effort(
        project: KippoProject,
        current_date: datetime.date = None,
        representative_day: int = TUESDAY_WEEKDAY) -> Tuple[List[dict], List[datetime.date]]:
    """
    Obtain the project weekly effort from the representative day.
    :param project: Project to calculate effort for
    :param current_date: date to start calculation for
    :param representative_day: Day of the week to use as the representative day in effort calculation
    """
    if not current_date:
        current_date = timezone.now().date()
    elif isinstance(current_date, datetime.datetime):
        current_date = current_date.date()

    if not project.start_date or not project.target_date:
        raise ProjectDatesError(f'{project.name} required dates not set: start_date={project.start_date}, target_date={project.target_date}')

    # get latest effort status
    # -- only a single entry per date

    # prepare dates
    search_dates = []
    start_date_calendar_info = project.start_date.isocalendar()
    start_date_year, start_date_week, _ = start_date_calendar_info
    # %W: Week number of the year (Monday as the first day of the week) as a decimal number. (0 start)
    # %w: Weekday as a decimal number, where 0 is Sunday
    # NOTE: isocalendar() returns the start week as (1 start), adjusting below to map to appropriate %W value
    initial_week_start_date = datetime.datetime.strptime(f'{start_date_year}-{start_date_week - 1}-{representative_day}', '%Y-%W-%w').date()
    current_week_start_date = initial_week_start_date
    logger.debug(f'initial_week_start_date: {initial_week_start_date}')
    assert current_week_start_date <= project.target_date
    while current_week_start_date <= project.target_date:
        search_dates.append(current_week_start_date)
        last_week_start_date = current_week_start_date
        current_week_start_date += datetime.timedelta(days=7)
        if last_week_start_date < current_date < current_week_start_date:
            # add the current date (to show the current status)
            search_dates.append(current_date)
    if project.target_date not in search_dates:
        search_dates.append(project.target_date)

    active_task_states = project.columnset.get_active_column_names()

    all_status_entries = []  # state__in=GITHUB_ACTIVE_TASK_STATES
    for current_week_start_date in search_dates:
        target_kippotaskstatus_ids = KippoTaskStatus.objects.filter(
            task__github_issue_api_url__isnull=False,  # filter out non-linked tasks
            task__project=project,
            effort_date__lte=current_week_start_date,
        ).order_by(
            'task__github_issue_api_url',
            '-effort_date'
        ).distinct('task__github_issue_api_url').values_list('pk', flat=True)

        previous_status_entries = KippoTaskStatus.objects.filter(
            pk__in=target_kippotaskstatus_ids,
            state__in=active_task_states
        ).values(
            'task__project',
            'effort_date',
            'task__assignee__github_login'
        ).annotate(task_count=Count('task'), estimate_days_sum=Coalesce(Sum('estimate_days'), Value(0)))
        all_status_entries.extend(list(previous_status_entries))

    if not all_status_entries:
        raise TaskStatusError(f'No KippoTaskStatus (has assignee.github_login, state__in={active_task_states}) found for project({project.name}) in ranges: '
                              f'{project.start_date} to {project.target_date}')

    return all_status_entries, search_dates


def prepare_project_plot_data(project: KippoProject, current_date: datetime.date = None):
    """
    Format data for easy plotting
    :param project:
    :param current_date:
    :return:
    """
    end_date = None
    data = defaultdict(list)
    burndown_line = None
    date_str_format = '%Y-%m-%d (%a)'
    if project.start_date and project.target_date and project.allocated_staff_days:
        start_date = project.start_date.strftime(date_str_format)  # Date formatted for display in graph
        data['effort_date'].append(start_date)
        end_date = project.target_date.strftime(date_str_format)  # Date formatted for display in graph
        start_staff_days = project.allocated_staff_days
        burndown_line_x = [start_date, end_date]
        burndown_line_y = [start_staff_days, 0]
        burndown_line = [burndown_line_x, burndown_line_y]

    logger.info(f'get_project_weekly_effort(): {project.name} ({current_date})')
    status_entries, all_dates = get_project_weekly_effort(project, current_date)

    assignees = set()
    logger.info(f'processing status_entries ({len(status_entries)})... ')
    for entry in status_entries:
        effort_date = entry['effort_date'].strftime(date_str_format)
        assignee = entry['task__assignee__github_login']
        estimate_days = entry['estimate_days_sum']
        if effort_date not in data['effort_date']:
            data['effort_date'].append(effort_date)

        effort_date_index = data['effort_date'].index(effort_date)
        while len(data[assignee]) < effort_date_index:
            data[assignee].append(0.0)  # back-fill
        data[assignee].append(estimate_days)
        assignees.add(assignee)

    # get max date
    logger.info('Collect all_date_strings and get max_date_str...')
    max_date_str = max(data['effort_date'])
    all_date_strings = [d.strftime(date_str_format) for d in all_dates]

    # updating for the case here start_date has not yet passed
    logger.info(f'Setting nearest_date_str to max_date_str: {max_date_str}')
    nearest_date_str = max_date_str
    if max_date_str not in all_date_strings:
        logger.warning(f'max_date_str({max_date_str}) not in all_date_strings, getting nearest date...')
        # find the nearest date to the max
        max_date = datetime.datetime.strptime(max_date_str, date_str_format)
        for date_str in sorted(all_date_strings, reverse=True):
            current_date = datetime.datetime.strptime(date_str, date_str_format)
            if current_date < max_date:
                # use this as the nearest date
                nearest_date_str = current_date.strftime(date_str_format)
                break
    logger.info(f'nearest_date_str: {nearest_date_str}')

    unadded_dates = all_date_strings[all_date_strings.index(nearest_date_str) + 1:]
    for date_str in unadded_dates:
        data['effort_date'].append(date_str)
        for assignee in assignees:
            data[assignee].append(0.0)
    return data, sorted(list(assignees)), burndown_line


def prepare_burndown_chart_components(project: KippoProject, current_date: datetime.date=None) -> tuple:
    """
    Prepare the javascript script and div for embedding into a template
    :param project: KippoProject
    :param current_date: Date to generate burndown FROM
    :return: script, div objects returned by
    """
    logger.info(f'prepare_project_plot_data(): {project.name} ({current_date})')
    data, assignees, burndown_line = prepare_project_plot_data(project, current_date)

    minimum_palette_count = 3  # property of the bokeh supplied palette choices
    required_color_count = len(assignees)
    color_count_index = required_color_count
    if required_color_count < minimum_palette_count:
        color_count_index = minimum_palette_count

    colors = all_palettes['Category20'][color_count_index][:required_color_count]

    logger.info(f'preparing figure:  {project.name}')
    p = figure(
        x_range=sorted(data['effort_date']),
        plot_height=300,
        plot_width=950,
        title=f"({project.name}) Project Weekly Work Estimates",
        toolbar_location=None,
        tools="hover",
        tooltips="$name @effort_date: @$name"
    )
    if burndown_line:
        p.line(
            *burndown_line,
            line_width=2,
            line_color='#BCBCBC',
            line_dash='dashed',
        )
    p.vbar_stack(
        assignees,
        x='effort_date',
        width=0.9,
        color=colors,
        source=data,
        legend=[value(user) for user in assignees]
    )

    p.y_range.start = 0
    p.yaxis.axis_label = 'Effort (days)'
    p.xaxis.axis_label = 'Dates (weekly)'
    p.xaxis.major_label_orientation = pi / 4
    p.x_range.range_padding = 0.1
    p.xgrid.grid_line_color = None
    p.axis.minor_tick_line_color = None
    p.outline_line_color = None
    p.legend.location = "top_right"
    p.legend.orientation = "vertical"

    script, div = components(p, CDN)
    return script, div
