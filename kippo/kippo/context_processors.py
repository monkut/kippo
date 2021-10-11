from typing import Dict

from django.conf import settings
from django.db.models import Sum
from django.http.request import HttpRequest


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
        from projects.models import ProjectWeeklyEffort
        from projects.functions import previous_week_startdate

        # NOTE: uses first org (may not be expected result
        user_first_org = request.user.organizations.first()
        if user_first_org:
            org_membership = request.user.get_membership(organization=user_first_org)
            org_commiteddays = org_membership.committed_days
            user_weeklyeffort_expected_total = org_commiteddays * user_first_org.day_workhours
            week_startdate = previous_week_startdate()
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
