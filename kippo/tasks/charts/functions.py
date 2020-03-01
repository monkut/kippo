import datetime
import logging
from typing import Dict, List, Tuple

from bokeh.embed import components
from bokeh.layouts import column
from bokeh.models import ColumnDataSource, DatetimeTicker, FactorRange, HoverTool, Label, Legend
from bokeh.models.glyphs import VBar
from bokeh.palettes import Category20
from bokeh.plotting import figure
from bokeh.resources import CDN

logger = logging.getLogger(__name__)

ONE_DAY = (3600000 * 24) - 25


def prepare_project_schedule_chart_components(
    project_data: List[Tuple[str, datetime.date, datetime.date, datetime.date, dict]],
    start_date: datetime.date,
    project_milestones: Dict[str, List[dict]] = None,
    display_days: int = 65,
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
    hover = HoverTool(
        names=["Scheduled Task", "Other Project Scheduled Task", "Unscheduled", "Uncommitted", "Holiday", "Weekend", "Personal Holiday"],
        tooltips=display_tooltips,
    )

    plots = []
    chart_minimum_height = 120
    chart_minimum_width = 155
    legend_added = False
    for project_id, project_start_date, project_target_date, project_estimated_date, data in project_data:
        legend_items = []
        logger.debug(f"preparing project_id; {project_id}")
        source = ColumnDataSource(data)
        y_range = set(data["project_assignee_grouped"])
        calculated_plot_height = (len(y_range) * 15) + chart_minimum_height
        calculated_plot_width = (display_days * 22) + chart_minimum_width
        difference = project_estimated_date - project_target_date
        positive_id = ""
        if difference.days > 0:
            positive_id = "+"
        p = figure(
            title=f"Target Completion={project_target_date}, Estimated Completion={project_estimated_date} ({positive_id}{difference})",
            y_range=FactorRange(*sorted(y_range)),
            x_range=(min_date, max_date),
            plot_width=calculated_plot_width,
            plot_height=calculated_plot_height,
            toolbar_location=None,
            tools=[hover],
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

        if project_start_date:  # if start_date is not defined this will be empty
            if project_start_date > min_date:
                logger.debug(f"project_start_date: {project_start_date}")
                glyph = VBar(x=project_start_date, top=calculated_plot_height, bottom=-50, width=ONE_DAY, line_color="green", fill_color=None)
                p.add_glyph(source, glyph)
                project_start_date_label = Label(
                    x=project_start_date, x_offset=label_x_offset, y=label_y, y_offset=0, text="Start", text_font_style="italic", text_font_size="8pt"
                )
                p.add_layout(project_start_date_label)
        if project_target_date:  # will be empty if not defined
            logger.debug(f"project_target_date: {project_target_date}")
            glyph = VBar(x=project_target_date, top=calculated_plot_height, bottom=-50, width=ONE_DAY, line_color="red", fill_color=None)
            p.add_glyph(source, glyph)
            project_target_date_label = Label(
                x=project_target_date, x_offset=label_x_offset, y=label_y, y_offset=0, text="Target", text_font_style="italic", text_font_size="8pt"
            )
            p.add_layout(project_target_date_label)

        # add assignments
        task_dates_entry_name = "Scheduled Task"
        task_dates_entry = p.square(
            name=task_dates_entry_name, y="project_assignee_grouped", x="task_dates", size=15, color="deepskyblue", source=source
        )
        legend_items.append((task_dates_entry_name, [task_dates_entry]))

        # add other scheduled
        scheduled_dates_entry_name = "Other Project Scheduled Task"
        scheduled_dates_entry = p.square_x(
            name=scheduled_dates_entry_name,
            y="project_assignee_grouped",
            x="scheduled_dates",
            size=15,
            fill_color=None,
            color="deepskyblue",
            alpha=0.5,
            source=source,
        )
        legend_items.append((scheduled_dates_entry_name, [scheduled_dates_entry]))

        # add unscheduled
        unscheduled_dates_entry_name = "Unscheduled"
        unscheduled_dates_entry = p.square(
            name=unscheduled_dates_entry_name,
            y="project_assignee_grouped",
            x="unscheduled_dates",
            size=15,
            fill_color=None,
            color="deepskyblue",
            source=source,
        )
        legend_items.append((unscheduled_dates_entry_name, [unscheduled_dates_entry]))

        # add uncommitted
        uncommitted_dates_entry_name = "Uncommitted"
        uncommitted_dates_entry = p.square_x(
            name=uncommitted_dates_entry_name,
            y="project_assignee_grouped",
            x="uncommitted_dates",
            size=15,
            fill_color=None,
            color="lightgrey",
            alpha=0.5,
            source=source,
        )
        legend_items.append((uncommitted_dates_entry_name, [uncommitted_dates_entry]))

        # add holidays
        holiday_dates_entry_name = "Holiday"
        holiday_dates_entry = p.circle(
            name=holiday_dates_entry_name, y="project_assignee_grouped", x="holiday_dates", size=15, color="lightgrey", alpha=0.5, source=source
        )
        legend_items.append((holiday_dates_entry_name, [holiday_dates_entry]))

        # add weekends
        weekend_dates_entry_name = "Weekend"
        weekend_dates_entry = p.circle(
            name=weekend_dates_entry_name, y="project_assignee_grouped", x="weekend_dates", size=15, color="lightgrey", source=source
        )
        legend_items.append((weekend_dates_entry_name, [weekend_dates_entry]))

        # add personal holidays
        personal_holiday_dates_entry_name = "Personal Holiday"
        personal_holiday_dates_entry = p.circle(
            name=personal_holiday_dates_entry_name,
            y="project_assignee_grouped",
            x="personal_holiday_dates",
            size=15,
            color="deepskyblue",
            source=source,
        )
        legend_items.append((personal_holiday_dates_entry_name, [personal_holiday_dates_entry]))

        glyph = VBar(x=project_estimated_date, top=calculated_plot_height, bottom=-50, width=ONE_DAY, line_color="grey", fill_color=None)
        p.add_glyph(source, glyph)

        label = Label(
            x=project_estimated_date,
            x_offset=label_x_offset,
            y=label_y,
            y_offset=1,
            text=f"Estimated ({positive_id}{difference.days} days)",
            text_font_style="italic",
            text_font_size="8pt",
        )
        p.add_layout(label)

        if not legend_added:
            legend = Legend(items=legend_items, location=(0, -60))
            p.add_layout(legend, "right")
            legend_added = True

        # taptool = p.select(type=TapTool)
        # taptool.callback = OpenURL(url="@task_urls")  # TODO: Finish!

        p.title.align = "right"
        p.title.text_font_style = "italic"
        p.title.text_font_size = "8pt"
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
