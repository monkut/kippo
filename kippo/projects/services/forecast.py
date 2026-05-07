"""Project completion forecast.

Computes an estimated completion date for a `KippoProject` given:

- past + in-progress logged effort (`ProjectWeeklyEffort.hours` ≤ today)
- future projected contribution from `ProjectMonthlyAssignment` rows for months
  strictly after the current month, distributed across each user's committed
  days-of-week (`OrganizationMembership.committed_weekdays`), minus public and
  personal holidays.

See decisions A1–A6, D5, O1–O3 in monkut/kippo#224.
"""

from __future__ import annotations

import datetime
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from accounts.models import KippoUser, OrganizationMembership, PersonalHoliday, PublicHoliday
from commons.functions import first_of_next_month
from django.db.models import Sum
from django.utils import timezone
from pydantic import BaseModel

from projects.models import ProjectMonthlyAssignment, ProjectWeeklyEffort

if TYPE_CHECKING:
    from projects.models import KippoProject

logger = logging.getLogger(__name__)

HORIZON_PADDING_DAYS = 31  # walk one month past the latest assignment month before giving up
PERSONAL_HOLIDAY_LOOKBACK_DAYS = 31  # PHs whose duration spans into the projection window


class ProjectStartDateRequiredError(ValueError):
    """Raised when forecast is requested for a project with no start_date."""


class ForecastResult(BaseModel):
    """Public result shape for ProjectAssignmentForecastManager.compute()."""

    estimated_completion_date: datetime.date | None
    delta_from_target_date_days: int | None
    target_date: datetime.date | None


@dataclass
class _UserContext:
    """Pre-fetched per-user inputs for the day-walking loop."""

    by_user_month: dict[int, dict[datetime.date, int]]
    user_membership: dict[int, OrganizationMembership]
    user_holiday_country: dict[int, int | None]
    public_holidays_by_country: dict[int, set[datetime.date]]
    user_personal_holidays: dict[int, set[datetime.date]]


class ProjectAssignmentForecastManager:
    """Compute the estimated completion date for a project.

    Wraps the forecast algorithm — past spend (logged effort) + future projection
    over `ProjectMonthlyAssignment` rows — behind a small public surface. Construct
    with a project, call `compute()` to get the dict payload.

    See monkut/kippo#224 decisions A1–A6, D5 for the algorithm details.
    """

    def __init__(self, project: KippoProject) -> None:
        self.project = project

    def compute(self) -> ForecastResult:
        """Return the forecast payload.

        Returns a `ForecastResult` with ``estimated_completion_date`` (date | None),
        ``delta_from_target_date_days`` (int | None — positive = behind target),
        and ``target_date`` (date | None — echo of project.target_date).

        Raises:
            ProjectStartDateRequiredError: if ``project.start_date`` is None.
        """
        project = self.project
        if project.start_date is None:
            raise ProjectStartDateRequiredError(f"project {project.pk} has no start_date")

        allocated_hours = project.allocated_effort_hours
        if not allocated_hours:
            return self.__build_response(None)

        today = timezone.localdate()
        spent_hours = self.__logged_hours_through(today)

        if spent_hours >= allocated_hours:
            return self.__build_response(self.__latest_logged_week_start(today) or today)

        next_month_start = first_of_next_month(today)
        future_assignments = list(ProjectMonthlyAssignment.objects.filter(project=project, month__gte=next_month_start).order_by("month"))
        if not future_assignments:
            return self.__build_response(None)

        horizon = max(a.month for a in future_assignments) + datetime.timedelta(days=HORIZON_PADDING_DAYS)
        ctx = self.__load_user_context(future_assignments, next_month_start, horizon)
        completion = self.__walk_to_completion(
            ctx=ctx,
            start_day=next_month_start,
            horizon=horizon,
            initial_hours=float(spent_hours),
            target_hours=float(allocated_hours),
            day_workhours=project.organization.day_workhours,
        )
        return self.__build_response(completion)

    def __build_response(self, completion_date: datetime.date | None) -> ForecastResult:
        delta = None
        if self.project.target_date and completion_date:
            delta = (completion_date - self.project.target_date).days
        return ForecastResult(
            estimated_completion_date=completion_date,
            delta_from_target_date_days=delta,
            target_date=self.project.target_date,
        )

    def __logged_hours_through(self, today: datetime.date) -> int:
        return ProjectWeeklyEffort.objects.filter(project=self.project, week_start__lte=today).aggregate(total=Sum("hours"))["total"] or 0

    def __latest_logged_week_start(self, today: datetime.date) -> datetime.date | None:
        latest = ProjectWeeklyEffort.objects.filter(project=self.project, week_start__lte=today).order_by("-week_start").first()
        return latest.week_start if latest else None

    def __load_user_context(
        self,
        future_assignments: list[ProjectMonthlyAssignment],
        next_month_start: datetime.date,
        horizon: datetime.date,
    ) -> _UserContext:
        organization = self.project.organization

        by_user_month: dict[int, dict[datetime.date, int]] = defaultdict(dict)
        for assignment in future_assignments:
            by_user_month[assignment.user_id][assignment.month] = assignment.percentage

        user_ids = list(by_user_month.keys())

        user_membership = {m.user_id: m for m in OrganizationMembership.objects.filter(user_id__in=user_ids, organization=organization)}

        user_holiday_country: dict[int, int | None] = {
            user.id: user.holiday_country_id or organization.default_holiday_country_id for user in KippoUser.objects.filter(id__in=user_ids)
        }

        public_holidays_by_country: dict[int, set[datetime.date]] = defaultdict(set)
        countries_used = {country_id for country_id in user_holiday_country.values() if country_id is not None}
        if countries_used:
            for ph in PublicHoliday.objects.filter(country_id__in=countries_used, day__gte=next_month_start, day__lte=horizon):
                public_holidays_by_country[ph.country_id].add(ph.day)

        user_personal_holidays: dict[int, set[datetime.date]] = defaultdict(set)
        for ph in PersonalHoliday.objects.filter(
            user_id__in=user_ids,
            day__gte=next_month_start - datetime.timedelta(days=PERSONAL_HOLIDAY_LOOKBACK_DAYS),
            day__lte=horizon,
        ):
            for offset in range(ph.duration):
                user_personal_holidays[ph.user_id].add(ph.day + datetime.timedelta(days=offset))

        return _UserContext(
            by_user_month=by_user_month,
            user_membership=user_membership,
            user_holiday_country=user_holiday_country,
            public_holidays_by_country=public_holidays_by_country,
            user_personal_holidays=user_personal_holidays,
        )

    @staticmethod
    def __user_contributes_on(ctx: _UserContext, user_id: int, day: datetime.date, weekday: int) -> int | None:
        """Return the user's assignment percentage for `day`, or None if they don't contribute."""
        month_key = day.replace(day=1)
        percentage = ctx.by_user_month.get(user_id, {}).get(month_key)
        if percentage is None:
            return None
        membership = ctx.user_membership.get(user_id)
        if membership is None or weekday not in membership.committed_weekdays:
            return None
        country_id = ctx.user_holiday_country.get(user_id)
        if country_id is not None and day in ctx.public_holidays_by_country.get(country_id, set()):
            return None
        if day in ctx.user_personal_holidays.get(user_id, set()):
            return None
        return percentage

    @classmethod
    def __walk_to_completion(
        cls,
        *,
        ctx: _UserContext,
        start_day: datetime.date,
        horizon: datetime.date,
        initial_hours: float,
        target_hours: float,
        day_workhours: int,
    ) -> datetime.date | None:
        cumulative = initial_hours
        current_day = start_day
        while current_day <= horizon:
            weekday = current_day.weekday()
            for user_id in ctx.by_user_month:
                percentage = cls.__user_contributes_on(ctx, user_id, current_day, weekday)
                if percentage is None:
                    continue
                cumulative += day_workhours * (percentage / 100.0)
            if cumulative >= target_hours:
                return current_day
            current_day += datetime.timedelta(days=1)
        return None
