import datetime
from http import HTTPStatus

from common.tests import DEFAULT_FIXTURES, setup_basic_project
from dateutil.relativedelta import relativedelta
from django.test import Client, TestCase
from django.utils import timezone

from ..models import Country, KippoOrganization, KippoUser, OrganizationMembership, PersonalHoliday, PublicHoliday
from ..views import _get_organization_monthly_available_workdays


class AccountsViewsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.holiday_country = Country(name="japan", alpha_2="jp", alpha_3="jpn", country_code="JPN", region="asia")
        self.holiday_country.save()

        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.user.holiday_country = self.holiday_country
        self.user.save()

        self.github_manager = KippoUser.objects.get(username="github-manager")
        self.other_organization = KippoOrganization.objects.create(
            name="other-test-organization",
            github_organization_name="isstaffmodeladmintestcasebase-other-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # add membership
        membership = OrganizationMembership(
            user=self.user, organization=self.other_organization, created_by=self.github_manager, updated_by=self.github_manager, is_developer=True
        )
        membership.save()
        self.nonmember_organization = KippoOrganization.objects.create(
            name="nonmember-test-organization",
            github_organization_name="isstaffmodeladmintestcasebase-nonmember-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.no_org_user = KippoUser(username="noorguser", github_login="noorguser", password="test", email="noorguser@github.com", is_staff=True)
        self.no_org_user.save()

        self.client = Client()

    def test___get_organization_monthly_available_workdays(self):
        organization_memberships, monthly_available_workdays = _get_organization_monthly_available_workdays(self.organization)
        self.assertEqual(len(organization_memberships), 1)
        two_years_plus_one_month = (12 * 2) + 1
        self.assertEqual(len(monthly_available_workdays.keys()), two_years_plus_one_month)

    def test___get_organization_monthly_available_workdays__publicholidays(self):
        current_datetime = timezone.now()
        start_datetime = datetime.datetime(current_datetime.year, current_datetime.month, 1, tzinfo=datetime.timezone.utc)
        two_years = 365 * 2
        two_years_from_now = start_datetime + datetime.timedelta(days=two_years)
        two_years_from_now += relativedelta(months=1)
        end_datetime = two_years_from_now.replace(day=1) - datetime.timedelta(days=1)

        # create public holidays
        current_date = start_datetime.date()
        end_date = end_datetime.date()
        while current_date <= end_date:
            holiday = PublicHoliday(country=self.holiday_country, name="test-holiday", day=current_date)
            holiday.save()
            current_date += datetime.timedelta(days=1)

        self.assertTrue(self.user.public_holiday_dates())

        # confirm that the total work days is 0 for user
        organization_memberships, monthly_available_workdays = _get_organization_monthly_available_workdays(self.organization)
        for month_key, member_available_workdays in monthly_available_workdays.items():
            for user, work_days in member_available_workdays.items():
                self.assertEqual(work_days, 0, f"{month_key} {user.github_login}")

    def test___get_organization_monthly_available_workdays__persionalholidays(self):
        current_datetime = timezone.now()
        start_datetime = datetime.datetime(current_datetime.year, current_datetime.month, 1, tzinfo=datetime.timezone.utc)
        two_years = 365 * 2
        two_years_from_now = start_datetime + datetime.timedelta(days=two_years)
        two_years_from_now += relativedelta(months=1)
        end_datetime = two_years_from_now.replace(day=1) - datetime.timedelta(days=1)

        # create public holidays
        current_date = start_datetime.date()
        end_date = end_datetime.date()
        while current_date <= end_date:
            holiday = PersonalHoliday(user=self.user, day=current_date)
            holiday.save()
            current_date += datetime.timedelta(days=1)

        self.assertTrue(self.user.personal_holiday_dates())

        # confirm that the total work days is 0 for user
        organization_memberships, monthly_available_workdays = _get_organization_monthly_available_workdays(self.organization)
        for month_key, member_available_workdays in monthly_available_workdays.items():
            for user, work_days in member_available_workdays.items():
                self.assertEqual(work_days, 0, f"{month_key} {user.github_login}")

    def test_view_organization_members(self):
        self.client.force_login(self.user)
        response = self.client.get("/accounts/members/")
        self.assertEqual(response.status_code, HTTPStatus.OK)
