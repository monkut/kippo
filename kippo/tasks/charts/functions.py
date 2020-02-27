import datetime
import logging
import time
from typing import List

from bokeh.embed import components
from bokeh.layouts import column
from bokeh.models import ColumnDataSource, DatetimeTicker, FactorRange, FixedTicker, Label, OpenURL, Span, TapTool
from bokeh.palettes import Category20
from bokeh.plotting import figure
from bokeh.resources import CDN

logger = logging.getLogger(__name__)


def prepare_project_schedule_chart_components(
    project_data: dict, start_date: datetime.date, project_milestones: List[dict] = None, display_days: int = 65
):
    """
    project_data =  {
        PROJECT_ID: {
            # length of columns expected to be the same
            'project_ids': [],
            'project_names': [],
            'assignees': [],
            'project_assignee_grouped': [],
            'task_ids': [],
            'task_urls': [],
            'task_titles': [],
            'task_estimate_days': [],
            'task_dates': [],
            'holiday_names': [],
            'weekend_dates': [],
            'scheduled_dates': [],
            'unscheduled_dates': [],
            'uncommitted_dates':[],
            'personal_holiday_dates': [],
        }

    }
    """

    # get min/max across all projects
    min_date = start_date - datetime.timedelta(days=1)
    max_date = min_date + datetime.timedelta(days=display_days)

    display_tooltips = [
        ("task", "@task_titles"),
        ("date", "@current_dates"),
        ("assignee", "@assignees"),
        ("estimate days", "@task_estimate_days"),
        ("description", "@descriptions"),
    ]

    plots = []
    for project_id, data in project_data.items():
        logger.debug(f"preparing project_id; {project_id}")
        source = ColumnDataSource(data)
        y_range = set(data["project_assignee_grouped"])
        calculated_plot_height = len(y_range) * 70

        p = figure(
            y_range=list(sorted(y_range)),  # FactorRange(*sorted(y_range)),
            x_range=(min_date, max_date),
            plot_width=display_days * 22,
            plot_height=calculated_plot_height,
            toolbar_location=None,
            tooltips=display_tooltips,
        )
        # p.hbar(y="project_assignee_grouped", left="task_start_dates", right="task_end_dates", height=0.4, source=source)
        # add assignments
        p.square(y="project_assignee_grouped", x="task_dates", size=15, color="deepskyblue", source=source)
        # add uncommitted
        p.square_x(y="project_assignee_grouped", x="scheduled_dates", size=15, fill_color=None, color="deepskyblue", alpha=0.2, source=source)
        # add unscheduled
        p.square(y="project_assignee_grouped", x="unscheduled_dates", size=15, fill_color=None, color="deepskyblue", source=source)
        # add uncommitted
        p.square_x(y="project_assignee_grouped", x="uncommitted_dates", size=15, fill_color=None, color="lightgrey", alpha=0.25, source=source)
        # add holidays
        p.circle(y="project_assignee_grouped", x="holiday_dates", size=15, color="lightgrey", alpha=0.5, source=source)
        # add weekends
        p.circle(y="project_assignee_grouped", x="weekend_dates", size=15, color="lightgrey", source=source)
        # add personal holidays
        p.circle(y="project_assignee_grouped", x="personal_holiday_dates", size=15, color="deepskyblue", source=source)

        taptool = p.select(type=TapTool)
        taptool.callback = OpenURL(url="@task_urls")  # TODO: Finish!

        # add milestones display
        if project_milestones and project_id in project_milestones:
            milestone_count = len(project_milestones[project_id])
            color_count = 3
            if milestone_count > 3:
                color_count = milestone_count
            color_palette = Category20[color_count]  # assumes milestones will not be > 20
            for idx, milestone_info in enumerate(project_milestones[project_id]):
                # bokeh requires this time format for display
                milestone_date = time.mktime(milestone_info["target_date"].timetuple()) * 1000
                milestone = Span(location=milestone_date, dimension="height", line_color=color_palette[idx], line_dash="dashed", line_width=3)
                p.add_layout(milestone)
                label = Label(
                    x=milestone_date, x_offset=5, y=0, y_offset=1, text=milestone_info["title"], text_font_style="italic", text_font_size="8pt"
                )
                p.add_layout(label)

        if "project_start_dates" in data and data["project_start_dates"]:  # if start_date is not defined this will be empty
            project_start_date = data["project_start_dates"][0]
            if project_start_date > min_date:
                logger.debug(f"project_start_date: {project_start_date}")
                project_start_date = time.mktime(project_start_date.timetuple()) * 1000  # bokeh requires this time format for display
                project_start = Span(location=project_start_date, dimension="height", line_color="green", line_dash="solid", line_width=2)
                p.add_layout(project_start)
                project_start_date_label = Label(
                    x=project_start_date, x_offset=-15, y=0, y_offset=1, text="Start", text_font_style="italic", text_font_size="8pt"
                )
                p.add_layout(project_start_date_label)
        if "project_target_dates" in data and data["project_target_dates"]:  # will be empty if not defined
            project_target_date = data["project_target_dates"][0]
            logger.debug(f"project_target_date: {project_target_date}")
            project_end_date = time.mktime(project_target_date.timetuple()) * 1000  # bokeh requires this time format for display
            project_end = Span(location=project_end_date, dimension="height", line_color="red", line_dash="solid", line_width=5)
            p.add_layout(project_end)
            project_target_date_label = Label(
                x=project_target_date, x_offset=5, y=0, y_offset=1, text="Target", text_font_style="italic", text_font_size="8pt"
            )
            p.add_layout(project_target_date_label)

        p.xaxis.ticker = DatetimeTicker()
        p.yaxis.group_label_orientation = "horizontal"
        p.ygrid.grid_line_color = None
        p.y_range.range_padding = 1
        p.y_range.range_padding_units = "absolute"
        p.yaxis.group_label_orientation = "horizontal"  # pi/25
        p.xaxis.axis_label = "Dates"
        p.outline_line_color = None
        plots.append(p)
    if len(plots) > 1:
        script, div = components(column(*plots), CDN)
    else:
        plot = plots[0]
        script, div = components(plot, CDN)
    return script, div
