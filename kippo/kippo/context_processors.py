from typing import Dict

from django.conf import settings
from django.db.models import Sum
from django.http.request import HttpRequest
from django.utils import timezone


def get_personal_holiday_hours(personal_holidays, day_workhours, end_date: timezone.datetime.date) -> float:
    total_days = 0
    for holiday in personal_holidays:
        if holiday.is_half:
            assert holiday.duration == 1
            total_days += 0.5
        elif holiday.duration > 1:
            current_date = holiday.day
            while current_date <= end_date:
                total_days += 1
                current_date += timezone.timedelta(days=1)
        else:
            total_days += 1
    personal_holiday_hours = total_days * day_workhours
    return personal_holiday_hours


def global_view_additional_context(request: HttpRequest) -> Dict:
    """
    context defined here is provided additionally to the template rendering contexxt

    :param request:
    :return:
    """
    user_weeklyeffort_hours_sum = None
    user_weeklyeffort_expected_total = None
    user_weeklyeffort_percentage = None
    if request.user and request.user.is_authenticated:
        from accounts.models import PublicHoliday, PersonalHoliday
        from projects.models import ProjectWeeklyEffort
        from projects.functions import previous_week_startdate

        # NOTE: uses first org (may not be expected result
        user_first_org = request.user.organizations.first()
        if user_first_org:
            org_membership = request.user.get_membership(organization=user_first_org)
            org_commiteddays = org_membership.committed_days
            user_weeklyeffort_expected_total = org_commiteddays * user_first_org.day_workhours

            week_startdate = previous_week_startdate()
            week_enddate = week_startdate + timezone.timedelta(days=4)

            # remove public holidays from total
            public_holidays = PublicHoliday.objects.filter(day__gte=week_startdate, day__lte=week_enddate).count()
            public_holiday_hours = public_holidays * user_first_org.day_workhours
            user_weeklyeffort_expected_total -= public_holiday_hours

            # remove personal holidays from total
            personal_holidays = PersonalHoliday.objects.filter(day__gte=week_startdate, day__lte=week_enddate)
            personal_holiday_hours = get_personal_holiday_hours(
                personal_holidays,
                day_workhours=user_first_org.day_workhours,
                end_date=week_enddate,
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
