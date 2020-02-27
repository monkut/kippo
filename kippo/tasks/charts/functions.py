import datetime
import logging
from typing import Dict, List

from bokeh.embed import components
from bokeh.layouts import column
from bokeh.models import ColumnDataSource, DatetimeTicker, FactorRange, Label, OpenURL, TapTool
from bokeh.models.glyphs import VBar
from bokeh.palettes import Category20
from bokeh.plotting import figure
from bokeh.resources import CDN

logger = logging.getLogger(__name__)

ONE_DAY = (3600000 * 24) - 25


def prepare_project_schedule_chart_components(
    project_data: dict, start_date: datetime.date, project_milestones: Dict[str, List[dict]] = None, display_days: int = 65
):
    """
    project_data =  {
        PROJECT_ID: {
            # length of columns expected to be the same
            'project_ids': [],
            'project_names': [],
            'project_start_dates': [],
            'project_target_dates': [],
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
    if project_milestones is None:
        project_milestones = {}

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
    chart_minimum_height = 100
    for project_id, data in project_data.items():
        logger.debug(f"preparing project_id; {project_id}")
        source = ColumnDataSource(data)
        y_range = set(data["project_assignee_grouped"])
        calculated_plot_height = (len(y_range) * 15) + chart_minimum_height

        p = figure(
            y_range=FactorRange(*sorted(y_range)),
            x_range=(min_date, max_date),
            plot_width=display_days * 22,
            plot_height=calculated_plot_height,
            toolbar_location=None,
            tooltips=display_tooltips,
        )
        project_specific_milestones = project_milestones.get(project_id, None)
        # add milestones display
        label_y = -0.75
        label_x_offset = 15
        if project_specific_milestones:
            milestone_count = len(project_specific_milestones)
            color_count = 3
            if milestone_count > 3:
                color_count = milestone_count
            color_palette = Category20[color_count]  # assumes milestones will not be > 20
            for idx, milestone_info in enumerate(project_specific_milestones):
                milestone_date = milestone_info["target_date"]
                glyph = VBar(x=milestone_date, top=calculated_plot_height, bottom=-50, width=ONE_DAY, line_color=color_palette[idx], fill_color=None)
                p.add_glyph(source, glyph)

                label = Label(
                    x=milestone_date,
                    x_offset=label_x_offset,
                    y=label_y,
                    y_offset=1,
                    text=milestone_info["title"],
                    text_font_style="italic",
                    text_font_size="8pt",
                )
                p.add_layout(label)

        if "project_start_dates" in data and data["project_start_dates"]:  # if start_date is not defined this will be empty
            project_start_date = data["project_start_dates"][0]
            if project_start_date > min_date:
                logger.debug(f"project_start_date: {project_start_date}")
                glyph = VBar(x=project_start_date, top=calculated_plot_height, bottom=-50, width=ONE_DAY, line_color="green", fill_color=None)
                p.add_glyph(source, glyph)
                project_start_date_label = Label(
                    x=project_start_date, x_offset=label_x_offset, y=label_y, y_offset=0, text="Start", text_font_style="italic", text_font_size="8pt"
                )
                p.add_layout(project_start_date_label)
        if "project_target_dates" in data and data["project_target_dates"]:  # will be empty if not defined
            project_target_date = data["project_target_dates"][0]
            logger.debug(f"project_target_date: {project_target_date}")
            glyph = VBar(x=project_target_date, top=calculated_plot_height, bottom=-50, width=ONE_DAY, line_color="red", fill_color=None)
            p.add_glyph(source, glyph)
            # p.vbar(x=project_target_date, top=calculated_plot_height, bottom=-50, width=.5, line_color='red', fill_color=None)
            project_target_date_label = Label(
                x=project_target_date, x_offset=label_x_offset, y=label_y, y_offset=0, text="Target", text_font_style="italic", text_font_size="8pt"
            )
            p.add_layout(project_target_date_label)

        # add assignments
        p.square(y="project_assignee_grouped", x="task_dates", size=15, color="deepskyblue", source=source)  # , legend_label="Scheduled Task")
        # add uncommitted
        p.square_x(y="project_assignee_grouped", x="scheduled_dates", size=15, fill_color=None, color="deepskyblue", alpha=0.2, source=source)  # ,
        # legend_label="Other Project Scheduled Task")
        # add unscheduled
        p.square(
            y="project_assignee_grouped", x="unscheduled_dates", size=15, fill_color=None, color="deepskyblue", source=source
        )  # , legend_label="Unscheduled")
        # add uncommitted
        p.square_x(y="project_assignee_grouped", x="uncommitted_dates", size=15, fill_color=None, color="lightgrey", alpha=0.25, source=source)
        # legend_label="Uncommitted")
        # add holidays
        p.circle(y="project_assignee_grouped", x="holiday_dates", size=15, color="lightgrey", alpha=0.5, source=source)  # , legend_label="Holiday")
        # add weekends
        p.circle(y="project_assignee_grouped", x="weekend_dates", size=15, color="lightgrey", source=source)  # , legend_label="Weekend")
        # add personal holidays
        p.circle(
            y="project_assignee_grouped", x="personal_holiday_dates", size=15, color="deepskyblue", source=source
        )  # , legend_label="Personal Holiday")

        taptool = p.select(type=TapTool)
        taptool.callback = OpenURL(url="@task_urls")  # TODO: Finish!

        p.xaxis.ticker = DatetimeTicker(desired_num_ticks=display_days)
        p.yaxis.group_label_orientation = "horizontal"
        p.ygrid.grid_line_color = None
        p.y_range.range_padding = 1
        p.y_range.range_padding_units = "absolute"
        p.yaxis.group_label_orientation = "horizontal"  # pi/25
        p.xaxis.axis_label = "Dates"
        p.xaxis.major_label_orientation = "vertical"
        p.outline_line_color = None
        plots.append(p)
    if len(plots) > 1:
        script, div = components(column(*plots), CDN)
    else:
        plot = plots[0]
        script, div = components(plot, CDN)
    return script, div
