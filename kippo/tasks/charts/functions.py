import time
import datetime
import logging

from bokeh.resources import CDN
from bokeh.embed import components
from bokeh.layouts import column
from bokeh.palettes import Category20
from bokeh.models import ColumnDataSource, FactorRange, FixedTicker, Span, Label
from bokeh.plotting import figure


logger = logging.getLogger(__name__)


def prepare_project_schedule_chart_components(project_data: dict, project_milestones: dict=None):

    # get min/max across all projects
    all_start_dates = []
    all_end_dates = []
    for project_id, data in project_data.items():
        all_start_dates.extend(data['task_start_dates'])
        all_end_dates.extend(data['project_target_dates'])
    min_date = min(all_start_dates) - datetime.timedelta(days=1)
    max_date = max(all_end_dates) + datetime.timedelta(days=5)

    # adjust for displayed x-axis ticks
    current_date = min_date
    xaxis_fixed_ticks = []
    while current_date <= max_date:
        current_date += datetime.timedelta(days=5)
        tick_date = time.mktime(current_date.timetuple()) * 1000
        xaxis_fixed_ticks.append(tick_date)

    display_tooltips = [
        ('task', '@task_titles'),
        ('assignee', '@assignees'),
        ('estimate days', '@task_estimate_days'),
    ]

    plots = []
    for project_id, data in project_data.items():
        source = ColumnDataSource(data)
        y_range = set(data['project_assignee_grouped'])
        calculated_plot_height = len(y_range) * 100

        p = figure(y_range=FactorRange(*sorted(y_range)),
                   x_range=(min_date, max_date),
                   plot_width=900,
                   plot_height=calculated_plot_height,
                   toolbar_location=None,
                   tooltips=display_tooltips)
        p.hbar(y='project_assignee_grouped',
               left='task_start_dates',
               right='task_end_dates',
               height=0.4,
               source=source)

        if project_id in project_milestones:
            milestone_count = len(project_milestones[project_id])
            color_count = 3
            if milestone_count > 3:
                color_count = milestone_count
            color_palette = Category20[color_count]  # assumes milestones will not be > 20
            for idx, milestone_info in enumerate(project_milestones[project_id]):
                # bokeh requires this time format for display
                milestone_date = time.mktime(milestone_info['target_date'].timetuple()) * 1000
                milestone = Span(location=milestone_date,
                                 dimension='height',
                                 line_color=color_palette[idx],
                                 line_dash='dashed',
                                 line_width=3)
                p.add_layout(milestone)
                label = Label(x=milestone_date,
                              x_offset=5,
                              y=0,
                              y_offset=1,
                              text=milestone_info['title'],
                              text_font_style='italic',
                              text_font_size='8pt')
                p.add_layout(label)

        if data['project_start_dates']:  # if start_date is not defined this will be empty
            project_start_date = data['project_start_dates'][0]
            if project_start_date > min_date:
                logger.debug(f'project_start_date: {project_start_date}')
                project_start_date = time.mktime(project_start_date.timetuple()) * 1000  # bokeh requires this time format for display
                project_start = Span(location=project_start_date,
                                   dimension='height',
                                   line_color='green',
                                   line_dash='solid',
                                   line_width=2)
                p.add_layout(project_start)
                project_start_date_label = Label(x=project_start_date,
                                                 x_offset=-15,
                                                 y=0,
                                                 y_offset=1,
                                                 text='Start',
                                                 text_font_style='italic',
                                                 text_font_size='8pt')
                p.add_layout(project_start_date_label)
        if data['project_target_dates']:  # will be empty if not defined
            project_target_date = data['project_target_dates'][0]
            logger.debug(f'project_target_date: {project_target_date}')
            project_end_date = time.mktime(project_target_date.timetuple()) * 1000  # bokeh requires this time format for display
            project_end = Span(location=project_end_date,
                               dimension='height',
                               line_color='red',
                               line_dash='solid',
                               line_width=5)
            p.add_layout(project_end)
            project_target_date_label = Label(x=project_target_date,
                                              x_offset=5,
                                              y=0,
                                              y_offset=1,
                                              text='Target',
                                              text_font_style='italic',
                                              text_font_size='8pt')
            p.add_layout(project_target_date_label)

        p.xaxis.ticker = FixedTicker(ticks=xaxis_fixed_ticks)
        p.yaxis.group_label_orientation = 'horizontal'
        p.ygrid.grid_line_color = None
        p.yaxis.group_label_orientation = 'horizontal'  # pi/25
        p.xaxis.axis_label = "Dates"
        p.outline_line_color = None
        plots.append(p)

    script, div = components(column(*plots), CDN)
    return script, div
