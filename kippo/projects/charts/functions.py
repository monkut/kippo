"""
For functions used to create project based charts
"""
import datetime
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
        raise ProjectDatesError(f'{project.name} required dates not set: start_date={project.start_date}, target_date={project.target_date}')

    # get latest effort status
    # -- only a single entry per date

    # prepare dates
    search_dates = []
    start_date_calendar_info = project.start_date.isocalendar()
    start_date_year, start_date_week, _ = start_date_calendar_info
    initial_week_start_date = datetime.datetime.strptime(f'{start_date_year}-{start_date_week}-{TUESDAY_WEEKDAY}', '%Y-%W-%w').date()
    current_week_start_date = initial_week_start_date

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
        previous_status_entries = KippoTaskStatus.objects.filter(task__project=project,
                                                                 task__assignee__github_login__isnull=False,
                                                                 effort_date=current_week_start_date,
                                                                 state__in=active_task_states).values('task__project', 'effort_date', 'task__assignee__github_login').annotate(task_count=Count('task'), estimate_days_sum=Coalesce(Sum('estimate_days'), Value(0)))

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
    end_date = None
    data = defaultdict(list)
    burndown_line = None
    if project.start_date and project.target_date and project.allocated_staff_days:
        start_date = project.start_date.strftime('%Y-%m-%d (%a)')  # Date formatted for display in graph
        data['effort_date'].append(start_date)
        end_date = project.target_date.strftime('%Y-%m-%d (%a)')  # Date formatted for display in graph
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
            data[assignee].append(0.0)  # back-fill
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


def prepare_burndown_chart_components(project: KippoProject, current_date: datetime.date=None) -> tuple:
    """
    Prepare the javascript script and div for embedding into a template
    :param project: KippoProject
    :param current_date: Date to generate burndown FROM
    :return: script, div objects returned by
    """
    data, assignees, burndown_line = prepare_project_plot_data(project, current_date)

    minimum_palette_count = 3  # property of the bokeh supplied palette choices
    required_color_count = len(assignees)
    color_count_index = required_color_count
    if required_color_count < minimum_palette_count:
        color_count_index = minimum_palette_count

    colors = all_palettes['Category20'][color_count_index][:required_color_count]

    p = figure(
        x_range=data['effort_date'],
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
