import datetime
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from projects.functions import get_user_session_organization

from .models import KippoOrganization, OrganizationMembership


def _get_organization_monthly_available_workdays(organization: KippoOrganization) -> Tuple[List[OrganizationMembership], Dict[str, Counter]]:
    # get organization memberships
    organization_memberships = list(
        OrganizationMembership.objects.filter(organization=organization, user__github_login__isnull=False, is_developer=True)
        .exclude(user__github_login__contains="unassigned")
        .order_by("user__github_login")
    )
    member_personal_holiday_dates = {m.user.github_login: tuple(m.user.personal_holiday_dates()) for m in organization_memberships}
    member_public_holiday_dates = {m.user.github_login: tuple(m.user.public_holiday_dates()) for m in organization_memberships}

    current_datetime = timezone.now()
    start_datetime = datetime.datetime(current_datetime.year, current_datetime.month, 1, tzinfo=datetime.timezone.utc)
    two_years = 365 * 2
    two_years_from_now = start_datetime + datetime.timedelta(days=two_years)

    # get the last full month 2 years from now
    end_datetime = two_years_from_now.replace(month=two_years_from_now.month + 1, day=1) - datetime.timedelta(days=1)

    current_date = start_datetime.date()
    end_date = end_datetime.date()

    monthly_available_workdays = defaultdict(Counter)
    while current_date <= end_date:
        month_key = current_date.strftime("%Y-%m")
        for membership in organization_memberships:
            if (
                current_date not in member_personal_holiday_dates[membership.user.github_login]
                and current_date not in member_public_holiday_dates[membership.user.github_login]
            ):
                if current_date.weekday() in membership.committed_weekdays:
                    monthly_available_workdays[month_key][membership.user] += 1
        current_date += datetime.timedelta(days=1)
    return organization_memberships, monthly_available_workdays


def view_organization_members(request):
    try:
        selected_organization, user_organizations = get_user_session_organization(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e.args))

    organization_memberships, monthly_available_workdays = _get_organization_monthly_available_workdays(selected_organization)

    # prepare monthly output for template
    monthly_member_data = []
    for month in sorted(monthly_available_workdays.keys()):
        data = (
            month,
            sum(monthly_available_workdays[month].values()),
            [monthly_available_workdays[month][m.user] for m in organization_memberships],  # get data in organization_membership order
        )
        monthly_member_data.append(data)

    context = {
        "selected_organization": selected_organization,
        "organizations": user_organizations,
        "organization_memberships": organization_memberships,
        "monthly_available_workdays": monthly_member_data,
        "messages": messages.get_messages(request),
    }

    return render(request, "accounts/view_organization_members.html", context)
