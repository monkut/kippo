"""Project effort / progress calculations.

Single home for the numbers shown by the REST API, the Django admin, the project status view and
the Slack report. Imported by `projects.models`, so this module must not import it at module level.
"""

import datetime
from collections.abc import Collection, Iterable
from typing import TYPE_CHECKING

from accounts.models import PublicHoliday
from django.conf import settings

if TYPE_CHECKING:
    from ..definitions import ProjectProgressStatus
    from ..models import KippoProject

SATURDAY_WEEKDAY = 5
ONE_DAY = datetime.timedelta(days=1)

# 稼働状況 level: current effort compared with the effort expected at this point of the schedule.
EFFORT_LEVEL_NEUTRAL = "neutral"  # nothing to compare
EFFORT_LEVEL_SUCCESS = "success"  # at or under the expected effort
EFFORT_LEVEL_WARNING = "warning"  # over the expected effort
EFFORT_LEVEL_DANGER = "danger"  # over by more than PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD percent
EFFORT_LEVELS = (EFFORT_LEVEL_NEUTRAL, EFFORT_LEVEL_SUCCESS, EFFORT_LEVEL_WARNING, EFFORT_LEVEL_DANGER)


def public_holidays_by_country(country_ids: Iterable[int]) -> dict[int, set[datetime.date]]:
    """Public-holiday dates per country id, in one query."""
    holidays: dict[int, set[datetime.date]] = {}
    country_ids = {country_id for country_id in country_ids if country_id}
    if not country_ids:
        return holidays
    for country_id, day in PublicHoliday.objects.filter(country_id__in=country_ids).values_list("country_id", "day"):
        holidays.setdefault(country_id, set()).add(day)
    return holidays


def project_period_holidays(project: "KippoProject") -> list[datetime.date]:
    """Public holidays of the project organization's country that fall inside the project period."""
    country = project.organization.default_holiday_country
    if not country:
        return []
    return list(
        PublicHoliday.objects.filter(country=country, day__gte=project.start_date, day__lte=project.target_date).values_list("day", flat=True)
    )


def expected_effort_hours(
    project: "KippoProject", at_dates: Iterable[datetime.date], holidays: Collection[datetime.date] | None = None
) -> dict[datetime.date, float | None]:
    """Effort hours the project is expected to have consumed by each of `at_dates`.

    The budget (`allocated_effort_hours`) is spread evenly over the workdays (weekdays that are not
    public holidays) between `start_date` and `target_date`. A date after `target_date` expects the
    whole budget. The value is None for a date before `start_date`, and for every date when the
    project has no period, no budget or no workdays.

    `holidays` may be a precomputed collection of holiday dates (only membership is tested), which
    saves the PublicHoliday query in list contexts. One walk over the project period serves every date.
    """
    expected: dict[datetime.date, float | None] = dict.fromkeys(at_dates)
    budget_hours = project.allocated_effort_hours
    if not (project.start_date and project.target_date and budget_hours):
        return expected
    if all(at_date < project.start_date for at_date in expected):
        return expected
    if holidays is None:
        holidays = project_period_holidays(project)

    workdays = 0
    workdays_by_date: dict[datetime.date, int] = {}
    current_date = project.start_date
    while current_date <= project.target_date:
        if current_date.weekday() < SATURDAY_WEEKDAY and current_date not in holidays:
            workdays += 1
        workdays_by_date[current_date] = workdays
        current_date += ONE_DAY
    if not workdays:
        return expected

    hours_per_workday = budget_hours / workdays
    for at_date in expected:
        if at_date >= project.start_date:
            expected[at_date] = hours_per_workday * workdays_by_date.get(at_date, workdays)
    return expected


def effort_level(progress_status: "ProjectProgressStatus") -> str:
    """稼働状況 level for a progress status. One of EFFORT_LEVELS."""
    difference_percentage = progress_status.get_difference_percentage()
    if difference_percentage is None:
        return EFFORT_LEVEL_NEUTRAL
    if difference_percentage > settings.PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD:
        return EFFORT_LEVEL_DANGER
    if difference_percentage > 0:
        return EFFORT_LEVEL_WARNING
    return EFFORT_LEVEL_SUCCESS


def effort_meter(progress_status: "ProjectProgressStatus") -> dict | None:
    """Values for the 稼働状況 meter, or None when the project has no budget or no expected effort.

    fill_percentage is current effort over max(budget, current effort): the budget ratio, capped at
    100% once the project is over budget.
    """
    if not (progress_status.allocated_effort_hours and progress_status.expected_effort_hours):
        return None
    current_hours = progress_status.current_effort_hours or 0
    max_hours = max(progress_status.allocated_effort_hours, current_hours)
    return {
        "level": effort_level(progress_status),
        "fill_percentage": round(current_hours / max_hours * 100, 1) if current_hours > 0 else 0,
        "difference_percentage": progress_status.get_difference_percentage(),
    }


def user_effort_shares(user_effort_rows: Iterable[dict], total_hours: float) -> list[dict]:
    """Each user's hours and share of the project's logged effort.

    `user_effort_rows` are `ProjectWeeklyEffort` rows grouped by user, carrying `user__id`,
    `user__username`, `user__first_name`, `user__last_name` and `user_hours`.
    """
    shares = []
    for row in user_effort_rows:
        display_name = f"{row['user__first_name'] or ''} {row['user__last_name'] or ''}".strip() or row["user__username"]
        user_hours = row["user_hours"] or 0
        shares.append(
            {
                "user_id": row["user__id"],
                "username": row["user__username"],
                "display_name": display_name,
                "hours": user_hours,
                "percentage": (user_hours / total_hours) * 100 if total_hours > 0 else 0,
            }
        )
    return shares
