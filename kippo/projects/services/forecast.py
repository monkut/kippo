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
from typing import TYPE_CHECKING

from accounts.models import KippoUser, OrganizationMembership, PersonalHoliday, PublicHoliday
from commons.functions import first_of_next_month
from django.db.models import Sum
from django.utils import timezone
from pydantic import BaseModel

from projects.definitions import ProjectAssignmentForecastUserContext
from projects.exceptions import ProjectStartDateRequiredError
from projects.models import ProjectMonthlyAssignment, ProjectWeeklyEffort

if TYPE_CHECKING:
    from projects.models import KippoProject

logger = logging.getLogger(__name__)

HORIZON_PADDING_DAYS = 31  # walk one month past the latest assignment month before giving up
PERSONAL_HOLIDAY_LOOKBACK_DAYS = 31  # PHs whose duration spans into the projection window


class ForecastResult(BaseModel):
    """Public result shape for ProjectAssignmentForecastManager.compute()."""

    estimated_completion_date: datetime.date | None
    delta_from_target_date_days: int | None
    target_date: datetime.date | None


class ProjectAssignmentForecastManager:
    """Compute the estimated completion date for a project.

    Wraps the forecast algorithm — past spend (logged effort) + future projection
    over `ProjectMonthlyAssignment` rows — behind a small public surface. Construct
    with a project, call `compute()` to get the dict payload.

    See monkut/kippo#224 decisions A1–A6, D5 for the algorithm details.
    """

    def __init__(self, project: KippoProject) -> None:
        self.project = project
        self.__logged_hours_cache: int | None = None
        self.__latest_logged_week_start_cache: datetime.date | None = None
        self.__latest_logged_week_start_loaded: bool = False
        # Memoizes the query-derived portion of the forecast user-context (membership,
        # holiday country, public + personal holidays) keyed by the inputs that determine
        # it: (frozenset(user_ids), next_month_start, horizon). The suggester reuses one
        # manager across ~28 compute() calls over the same users/horizon (seed + binary
        # search + s_max + final), so this collapses ~110 identical queries to 4.
        # Instance-scoped only — managers are constructed per request, so this is Lambda-safe.
        self.__user_context_cache: dict[tuple, tuple[dict, dict, dict, dict]] = {}

    def compute(self, overlay: dict[int, dict[datetime.date, int]] | None = None) -> ForecastResult:
        """Return the forecast payload.

        Args:
            overlay: Optional `{user_id: {month_first_day: percentage}}` mapping. When
                provided, the future projection uses this hypothetical assignment set
                instead of querying `ProjectMonthlyAssignment` rows on the project.
                Used by the suggestion engine to evaluate candidate patterns without
                persisting them. Past + in-progress logged effort is still computed
                from `ProjectWeeklyEffort` regardless.

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
        if overlay is None:
            future_rows = list(ProjectMonthlyAssignment.objects.filter(project=project, month__gte=next_month_start).order_by("month"))
            if not future_rows:
                return self.__build_response(None)
            by_user_month: dict[int, dict[datetime.date, int]] = defaultdict(dict)
            for assignment in future_rows:
                by_user_month[assignment.user_id][assignment.month] = assignment.percentage
        else:
            by_user_month = {
                user_id: {month: pct for month, pct in months.items() if month >= next_month_start} for user_id, months in overlay.items()
            }
            by_user_month = {user_id: months for user_id, months in by_user_month.items() if months}
            if not by_user_month:
                return self.__build_response(None)

        all_months = [m for months in by_user_month.values() for m in months]
        horizon = max(all_months) + datetime.timedelta(days=HORIZON_PADDING_DAYS)
        ctx = self.__load_user_context(by_user_month, next_month_start, horizon)
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
        # Cached per manager instance — the suggester reuses one manager across many compute()
        # calls (one per candidate pattern + thin-pattern retries), and the logged-hours total
        # doesn't depend on the overlay being evaluated.
        if self.__logged_hours_cache is None:
            self.__logged_hours_cache = (
                ProjectWeeklyEffort.objects.filter(project=self.project, week_start__lte=today).aggregate(total=Sum("hours"))["total"] or 0
            )
        return self.__logged_hours_cache

    def __latest_logged_week_start(self, today: datetime.date) -> datetime.date | None:
        if not self.__latest_logged_week_start_loaded:
            latest = ProjectWeeklyEffort.objects.filter(project=self.project, week_start__lte=today).order_by("-week_start").first()
            self.__latest_logged_week_start_cache = latest.week_start if latest else None
            self.__latest_logged_week_start_loaded = True
        return self.__latest_logged_week_start_cache

    def __load_user_context(
        self,
        by_user_month: dict[int, dict[datetime.date, int]],
        next_month_start: datetime.date,
        horizon: datetime.date,
    ) -> ProjectAssignmentForecastUserContext:
        user_ids = list(by_user_month.keys())
        cache_key = (frozenset(user_ids), next_month_start, horizon)
        cached = self.__user_context_cache.get(cache_key)
        if cached is None:
            cached = self.__load_user_context_queries(user_ids, next_month_start, horizon)
            self.__user_context_cache[cache_key] = cached
        user_membership, user_holiday_country, public_holidays_by_country, user_personal_holidays = cached

        return ProjectAssignmentForecastUserContext(
            by_user_month=by_user_month,
            user_membership=user_membership,
            user_holiday_country=user_holiday_country,
            public_holidays_by_country=public_holidays_by_country,
            user_personal_holidays=user_personal_holidays,
        )

    def __load_user_context_queries(
        self,
        user_ids: list[int],
        next_month_start: datetime.date,
        horizon: datetime.date,
    ) -> tuple[dict, dict, dict, dict]:
        """Run the 4 DB queries behind the forecast user-context. Memoized by the caller."""
        organization = self.project.organization

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

        return user_membership, user_holiday_country, public_holidays_by_country, user_personal_holidays

    @staticmethod
    def __user_contributes_on(ctx: ProjectAssignmentForecastUserContext, user_id: int, day: datetime.date, weekday: int) -> int | None:
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
        ctx: ProjectAssignmentForecastUserContext,
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
