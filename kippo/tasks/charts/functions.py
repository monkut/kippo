import datetime

from bokeh.resources import CDN
from bokeh.embed import components
from bokeh.models import ColumnDataSource, FactorRange
from bokeh.plotting import figure


def prepare_project_schedule_chart_components(data: dict):
    min_date = min(data['task_start_dates']) - datetime.timedelta(days=1)
    max_date = max(data['task_end_dates']) + datetime.timedelta(days=5)

    display_tooltips = [
        ('task', '@task_titles'),
        ('assignee', '@assignees'),
        ('estimate days', '@task_estimate_days'),
    ]
    y_range = set(data['project_assignee_grouped'])

    source = ColumnDataSource(data)
    calculated_plot_height = len(y_range) * 125

    p = figure(y_range=FactorRange(*y_range),
               x_range=(min_date, max_date),
               plot_width=1250,
               plot_height=calculated_plot_height,
               toolbar_location=None,
               tooltips=display_tooltips,
               title="Project Task Schedule")
    p.hbar(y='project_assignee_grouped',
           left='task_start_dates',
           right='task_end_dates',
           height=0.4,
           source=source)

    p.yaxis.group_label_orientation = 'horizontal'
    p.ygrid.grid_line_color = None
    p.xaxis.axis_label = "Dates"
    p.outline_line_color = None

    script, div = components(p, CDN)
    return script, div
