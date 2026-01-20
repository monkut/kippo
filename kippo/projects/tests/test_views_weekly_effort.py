import datetime

from accounts.models import Country, OrganizationMembership, PersonalHoliday, PublicHoliday
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from freezegun import freeze_time
from rest_framework.test import APIClient


class WeeklyEffortMissingWeeksViewTestCase(TestCase):
    """Tests for WeeklyEffortMissingWeeksView all-holiday week exclusion."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.holiday_country = Country(name="japan", alpha_2="jp", alpha_3="jpn", country_code="JPN", region="asia")
        self.holiday_country.save()

        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.organization.fiscalyear_start_month = 4  # April
        self.organization.default_holiday_country = self.holiday_country
        self.organization.save()

        self.user = created["KippoUser"]
        self.user.holiday_country = self.holiday_country
        self.user.save()

        # Get membership and set committed weekdays (Mon-Fri)
        self.membership = OrganizationMembership.objects.get(user=self.user, organization=self.organization)
        self.membership.monday = True
        self.membership.tuesday = True
        self.membership.wednesday = True
        self.membership.thursday = True
        self.membership.friday = True
        self.membership.saturday = False
        self.membership.sunday = False
        self.membership.save()

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @freeze_time("2024-04-15")  # Monday in second week of fiscal year
    def test_missing_weeks__includes_week_without_holidays(self):
        """Weeks without any effort entries should appear as missing."""
        response = self.client.get("/api/weekly-effort/missing-weeks/")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # First week of fiscal year (2024-04-01 is Monday) should be missing
        self.assertIn("2024-04-01", data["missing_weeks"])
        # Second week should not be missing (it's the current week)
        self.assertNotIn("2024-04-08", data["missing_weeks"])

    @freeze_time("2024-04-15")  # Monday in second week of fiscal year
    def test_missing_weeks__excludes_week_with_all_public_holidays(self):
        """Weeks where all committed weekdays are public holidays should not appear as missing."""
        # Create public holidays for all weekdays in the first week (Apr 1-5, 2024)
        week_start = datetime.date(2024, 4, 1)
        for day_offset in range(5):  # Mon-Fri
            PublicHoliday.objects.create(
                country=self.holiday_country,
                name=f"Holiday {day_offset}",
                day=week_start + datetime.timedelta(days=day_offset),
            )

        response = self.client.get("/api/weekly-effort/missing-weeks/")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # First week should NOT be in missing weeks (all days are public holidays)
        self.assertNotIn("2024-04-01", data["missing_weeks"])

    @freeze_time("2024-04-15")
    def test_missing_weeks__excludes_week_with_all_personal_holidays(self):
        """Weeks where all committed weekdays are personal holidays should not appear as missing."""
        # Create personal holiday spanning full first week
        PersonalHoliday.objects.create(
            user=self.user,
            day=datetime.date(2024, 4, 1),
            duration=5,  # Mon-Fri
            is_half=False,
            created_by=self.user,
            updated_by=self.user,
        )

        response = self.client.get("/api/weekly-effort/missing-weeks/")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # First week should NOT be in missing weeks (all days are personal holidays)
        self.assertNotIn("2024-04-01", data["missing_weeks"])

    @freeze_time("2024-04-15")
    def test_missing_weeks__excludes_week_with_mixed_holidays_covering_all_days(self):
        """Weeks with combination of public and personal holidays covering all days should not appear as missing."""
        week_start = datetime.date(2024, 4, 1)

        # Mon-Wed are public holidays
        for day_offset in range(3):
            PublicHoliday.objects.create(
                country=self.holiday_country,
                name=f"Holiday {day_offset}",
                day=week_start + datetime.timedelta(days=day_offset),
            )

        # Thu-Fri are personal holidays
        PersonalHoliday.objects.create(
            user=self.user,
            day=datetime.date(2024, 4, 4),  # Thursday
            duration=2,
            is_half=False,
            created_by=self.user,
            updated_by=self.user,
        )

        response = self.client.get("/api/weekly-effort/missing-weeks/")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # First week should NOT be in missing weeks (all days covered by holidays)
        self.assertNotIn("2024-04-01", data["missing_weeks"])

    @freeze_time("2024-04-15")
    def test_missing_weeks__includes_week_with_partial_holidays(self):
        """Weeks with only partial holiday coverage should appear as missing."""
        week_start = datetime.date(2024, 4, 1)

        # Only Mon-Wed are public holidays (Thu-Fri are working days)
        for day_offset in range(3):
            PublicHoliday.objects.create(
                country=self.holiday_country,
                name=f"Holiday {day_offset}",
                day=week_start + datetime.timedelta(days=day_offset),
            )

        response = self.client.get("/api/weekly-effort/missing-weeks/")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # First week SHOULD be in missing weeks (Thu-Fri are not holidays)
        self.assertIn("2024-04-01", data["missing_weeks"])

    @freeze_time("2024-04-15")
    def test_missing_weeks__respects_committed_weekdays(self):
        """Only committed weekdays should be considered when checking for all-holiday weeks."""
        # User only works Mon-Wed
        self.membership.thursday = False
        self.membership.friday = False
        self.membership.save()

        week_start = datetime.date(2024, 4, 1)

        # Only Mon-Wed are public holidays
        for day_offset in range(3):
            PublicHoliday.objects.create(
                country=self.holiday_country,
                name=f"Holiday {day_offset}",
                day=week_start + datetime.timedelta(days=day_offset),
            )

        response = self.client.get("/api/weekly-effort/missing-weeks/")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # First week should NOT be in missing weeks (all committed days are holidays)
        self.assertNotIn("2024-04-01", data["missing_weeks"])
