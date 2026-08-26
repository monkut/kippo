import datetime
import logging
from collections.abc import Container, Iterable
from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Sum
from django.http.request import HttpRequest
from django.utils import timezone

from commons.definitions import PERSONAL_HOLIDAY_LOOKBACK_DAYS, SATURDAY

if TYPE_CHECKING:
    from accounts.models import PersonalHoliday


logger = logging.getLogger(__name__)


def get_personal_holiday_workday_fractions(
    personal_holidays: Iterable["PersonalHoliday"],
    start_date: datetime.date,
    end_date: datetime.date,
) -> dict[datetime.date, float]:
    """Map each business day covered by `personal_holidays` to the fraction of a day taken.

    `PersonalHoliday.duration` is INCLUSIVE of `day`: a holiday covers
    `day` ... `day + duration - 1`. Dates outside `[start_date, end_date]` and
    weekends are dropped; overlapping holidays collapse onto the same date rather
    than being counted twice.
    """
    covered: dict[datetime.date, float] = {}
    for holiday in personal_holidays:
        duration = max(1, holiday.duration)
        # `is_half` is only meaningful for a single-day holiday (the admin and the API
        # only ever produce that combination); a multi-day span is always full days.
        fraction = 0.5 if holiday.is_half and duration == 1 else 1.0
        for offset in range(duration):
            target_date = holiday.day + datetime.timedelta(days=offset)
            if target_date < start_date or target_date > end_date:
                continue
            if target_date.weekday() >= SATURDAY:  # business days (Monday to Friday) only
                continue
            covered[target_date] = max(covered.get(target_date, 0.0), fraction)
    return covered


def get_personal_holiday_hours(
    personal_holidays: Iterable["PersonalHoliday"],
    day_workhours: float,
    start_date: datetime.date,
    end_date: datetime.date,
    exclude_dates: Container[datetime.date] = (),
) -> float:
    """Hours to deduct for personal holidays falling within `[start_date, end_date]`.

    Pass the window's public holidays as `exclude_dates` so a personal holiday
    covering one is not deducted twice by the caller.
    """
    covered = get_personal_holiday_workday_fractions(personal_holidays, start_date=start_date, end_date=end_date)
    total_days = sum(fraction for day, fraction in covered.items() if day not in exclude_dates)
    return total_days * day_workhours


def global_view_additional_context(request: HttpRequest) -> dict:
    """
    Context defined here is provided additionally to the template rendering contexxt

    :param request:
    :return:
    """
    user_weeklyeffort_hours_sum = None
    user_weeklyeffort_expected_total = None
    user_weeklyeffort_percentage = None
    if request.user and request.user.is_authenticated:
        from accounts.models import PersonalHoliday, PublicHoliday
        from projects.functions import previous_week_startdate
        from projects.models import ProjectWeeklyEffort

        # NOTE: uses first org (may not be expected result
        user_first_org = request.user.organizations.first()
        if user_first_org:
            org_membership = request.user.get_membership(organization=user_first_org)
            org_commiteddays = org_membership.committed_days
            user_weeklyeffort_expected_total = org_commiteddays * user_first_org.day_workhours

            week_startdate = previous_week_startdate()
            week_enddate = week_startdate + timezone.timedelta(days=4)

            # remove public holidays from total
            public_holidays = PublicHoliday.objects.filter(
                day__gte=week_startdate,
                day__lte=week_enddate,
            )
            if request.user.holiday_country:
                public_holidays = public_holidays.filter(country=request.user.holiday_country)
            elif org_membership and org_membership.organization.default_holiday_country:
                public_holidays = public_holidays.filter(country=org_membership.organization.default_holiday_country)

            public_holiday_dates = set(public_holidays.values_list("day", flat=True))
            public_holiday_hours = len(public_holiday_dates) * user_first_org.day_workhours
            user_weeklyeffort_expected_total -= public_holiday_hours

            # remove personal holidays from total
            # NOTE: filtered from a lookback -- the filter matches the holiday's START date, so a
            # span beginning before the week must be included for its in-week days to be deducted.
            personal_holidays = PersonalHoliday.objects.filter(
                user=request.user,
                day__gte=week_startdate - timezone.timedelta(days=PERSONAL_HOLIDAY_LOOKBACK_DAYS),
                day__lte=week_enddate,
            )
            personal_holiday_hours = get_personal_holiday_hours(
                personal_holidays,
                day_workhours=user_first_org.day_workhours,
                start_date=week_startdate,
                end_date=week_enddate,
                exclude_dates=public_holiday_dates,
            )

            user_weeklyeffort_expected_total -= personal_holiday_hours

            user_weeklyeffort_hours_result = ProjectWeeklyEffort.objects.filter(user=request.user, week_start=week_startdate).aggregate(Sum("hours"))
            if user_weeklyeffort_hours_result and "hours__sum" in user_weeklyeffort_hours_result:
                user_weeklyeffort_hours_sum = user_weeklyeffort_hours_result["hours__sum"]
                if user_weeklyeffort_hours_sum and user_weeklyeffort_hours_sum >= 0 and user_weeklyeffort_expected_total > 0:
                    user_weeklyeffort_percentage = int((user_weeklyeffort_hours_sum / user_weeklyeffort_expected_total) * 100)

    context = {
        "URL_PREFIX": settings.URL_PREFIX,
        "STATIC_URL": settings.STATIC_URL,
        "DISPLAY_ADMIN_AUTH_FOR_MODELBACKEND": settings.DISPLAY_ADMIN_AUTH_FOR_MODELBACKEND,
        "USER_WEEKLYEFFORT_HOURS_TOTAL": user_weeklyeffort_hours_sum,
        "USER_WEEKLYEFFORT_HOURS_EXPECTED": user_weeklyeffort_expected_total,
        "USER_WEEKLYEFFORT_HOURS_PERCENTAGE": user_weeklyeffort_percentage,
    }
    return context
