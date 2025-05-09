from django.test import TestCase
from django.utils import timezone

from accounts.models import Country, EmailDomain, KippoOrganization, KippoUser, PersonalHoliday, PublicHoliday


class PersonalHolidayTestCase(TestCase):
    fixtures = ["required_bot_users", "default_columnset", "default_labelset"]

    def setUp(self):
        self.maxDiff = None

        self.holiday_country = Country(name="japan", alpha_2="jp", alpha_3="jpn", country_code="JPN", region="asia")
        self.holiday_country.save()

        self.user = KippoUser(username="accounts-octocat", password="test", email="accounts@github.com", is_staff=True)  # noqa: S106
        self.user.holiday_country = self.holiday_country
        self.user.save()

        self.org = KippoOrganization(name="some org", github_organization_name="some-org", created_by=self.user, updated_by=self.user)
        self.org.save()
        self.domain = "kippo.org"
        self.emaildomain = EmailDomain(organization=self.org, domain=self.domain, is_staff_domain=True, created_by=self.user, updated_by=self.user)
        self.emaildomain.save()

        self.nonstaff_org = KippoOrganization(
            name="nonstaff org", github_organization_name="nonstaff-org", created_by=self.user, updated_by=self.user
        )
        self.nonstaff_org.save()
        self.nonstaff_org_domain = "nonstaff.org"
        self.emaildomain = EmailDomain(
            organization=self.nonstaff_org, domain=self.nonstaff_org_domain, is_staff_domain=False, created_by=self.user, updated_by=self.user
        )
        self.emaildomain.save()

    def test_get_weeklyeffort_hours__same_weekstart(self):
        start = timezone.datetime(2022, 8, 24).date()
        week_start = timezone.datetime(2022, 8, 22).date()
        days = 3
        holiday = PersonalHoliday.objects.create(user=self.user, day=start, duration=days)
        results = list(holiday.get_weeklyeffort_hours())
        self.assertEqual(len(results), 1)
        result = results[0]
        expected = {
            "project": "PersonalHoliday",
            "week_start": week_start.strftime("%Y%m%d"),
            "user": self.user.display_name,
            "hours": 8 * days,
        }
        self.assertDictEqual(result, expected)

    def test_get_weeklyeffort_hours__weekstart_span(self):
        start = timezone.datetime(2022, 8, 24).date()
        week_start = timezone.datetime(2022, 8, 22).date()
        days = 6
        holiday = PersonalHoliday.objects.create(user=self.user, day=start, duration=days)
        results = list(holiday.get_weeklyeffort_hours())
        self.assertEqual(len(results), 2)
        first = results[0]
        first_expected = {
            "project": "PersonalHoliday",
            "week_start": week_start.strftime("%Y%m%d"),
            "user": self.user.display_name,
            "hours": 8 * 3,
        }
        self.assertDictEqual(first, first_expected)

        second_week_start = timezone.datetime(2022, 8, 29).date()
        second_expected = {
            "project": "PersonalHoliday",
            "week_start": second_week_start.strftime("%Y%m%d"),
            "user": self.user.display_name,
            "hours": 8,
        }
        second = results[1]
        self.assertDictEqual(second, second_expected)

    def test_get_weeklyeffort_hours__weekstart_span__with_publicholiday(self):
        PublicHoliday.objects.create(country=self.holiday_country, name="test holiday", day=timezone.datetime(2022, 8, 29).date())
        start = timezone.datetime(2022, 8, 24).date()
        week_start = timezone.datetime(2022, 8, 22).date()
        days = 6
        holiday = PersonalHoliday.objects.create(user=self.user, day=start, duration=days)

        calculation_date = timezone.datetime(2022, 8, 1).date()
        results = list(holiday.get_weeklyeffort_hours(today=calculation_date))
        self.assertEqual(len(results), 1)
        first = results[0]
        first_expected = {
            "project": "PersonalHoliday",
            "week_start": week_start.strftime("%Y%m%d"),
            "user": self.user.display_name,
            "hours": 8 * 3,
        }
        self.assertDictEqual(first, first_expected)
