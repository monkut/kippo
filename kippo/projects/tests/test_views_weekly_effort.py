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

    @freeze_time("2024-04-08")  # Monday of second week of fiscal year
    def test_missing_weeks__includes_week_without_holidays(self):
        """Weeks without any effort entries should appear as missing."""
        response = self.client.get("/api/weekly-effort/missing-weeks/")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # First week of fiscal year (2024-04-01 is Monday) should be missing
        self.assertIn("2024-04-01", data["missing_weeks"])
        # Current week (2024-04-08) should not be in missing weeks
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


class WeeklyEffortExpectedHoursViewTestCase(TestCase):
    """Tests for WeeklyEffortExpectedHoursView personal-holiday deduction.

    Regression coverage for kiconiaworks/kippo#58: `get_personal_holiday_hours()`
    bounded its walk by `end_date` instead of `PersonalHoliday.duration`, so a
    multi-day holiday consumed every business day from its start to the end of the
    week. `duration` is INCLUSIVE of `day` -- a holiday covers
    `day` ... `day + duration - 1`.

    The fixture org has day_workhours=8 and the member is committed Mon-Fri, so the
    unreduced expectation is 40 hours.
    """

    fixtures = DEFAULT_FIXTURES

    # 2024-04-01 is a Monday.
    WEEK_START = datetime.date(2024, 4, 1)

    def setUp(self):
        self.holiday_country = Country(name="japan", alpha_2="jp", alpha_3="jpn", country_code="JPN", region="asia")
        self.holiday_country.save()

        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.organization.default_holiday_country = self.holiday_country
        self.organization.save()

        self.user = created["KippoUser"]
        self.user.holiday_country = self.holiday_country
        self.user.save()

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

    def _create_personal_holiday(self, day: datetime.date, duration: int = 1, is_half: bool = False) -> PersonalHoliday:
        return PersonalHoliday.objects.create(
            user=self.user,
            day=day,
            duration=duration,
            is_half=is_half,
            created_by=self.user,
            updated_by=self.user,
        )

    def _get_expected_hours(self, week_start: datetime.date | None = None) -> float:
        response = self.client.get("/api/weekly-effort/expected-hours/", {"week_start": (week_start or self.WEEK_START).isoformat()})
        self.assertEqual(response.status_code, 200)
        return response.json()["expected_hours"]

    def test_expected_hours__no_holidays(self):
        """5 committed days x 8 hours."""
        self.assertEqual(self._get_expected_hours(), 40)

    def test_expected_hours__multiday_holiday_deducts_only_its_duration(self):
        """A 2-day holiday deducts 2 days, not every business day through Friday.

        Before the fix this walked Mon->Fri and deducted 40 hours, reporting 0.
        """
        self._create_personal_holiday(self.WEEK_START, duration=2)  # Mon + Tue

        self.assertEqual(self._get_expected_hours(), 24)

    def test_expected_hours__multiday_holiday_uses_organization_day_workhours(self):
        """The per-day rate comes from the organization, not a hardcoded 8."""
        self.organization.day_workhours = 7
        self.organization.save()
        self._create_personal_holiday(self.WEEK_START, duration=2)  # Mon + Tue

        # 5 committed days x 7 = 35, minus 2 days x 7 = 14.
        self.assertEqual(self._get_expected_hours(), 21)

    def test_expected_hours__single_day_holiday(self):
        self._create_personal_holiday(self.WEEK_START, duration=1)

        self.assertEqual(self._get_expected_hours(), 32)

    def test_expected_hours__half_day_holiday(self):
        self._create_personal_holiday(self.WEEK_START, duration=1, is_half=True)

        self.assertEqual(self._get_expected_hours(), 36)

    def test_expected_hours__span_starting_before_the_week_is_deducted(self):
        """A span opening the previous Friday still consumes this week's Monday.

        The queryset filters on the holiday's START date, so before the fix this
        holiday was not selected at all and nothing was deducted.
        """
        # Fri 2024-03-29 + 4 days -> Fri, Sat, Sun, Mon(04-01).
        self._create_personal_holiday(datetime.date(2024, 3, 29), duration=4)

        self.assertEqual(self._get_expected_hours(), 32)

    def test_expected_hours__weekend_and_out_of_window_days_are_not_deducted(self):
        """Only business days inside the Mon-Fri window count."""
        # Thu 2024-04-04 + 5 days -> Thu, Fri, Sat, Sun, Mon(04-08).
        # Only Thu + Fri fall in this week's window.
        self._create_personal_holiday(datetime.date(2024, 4, 4), duration=5)

        self.assertEqual(self._get_expected_hours(), 24)

    def test_expected_hours__overlapping_holidays_counted_once(self):
        """Two overlapping spans collapse onto the same dates rather than double-deducting."""
        self._create_personal_holiday(self.WEEK_START, duration=3)  # Mon-Wed
        self._create_personal_holiday(datetime.date(2024, 4, 3), duration=2)  # Wed-Thu

        # Union is Mon, Tue, Wed, Thu -> 4 days.
        self.assertEqual(self._get_expected_hours(), 8)

    def test_expected_hours__public_holiday_inside_personal_span_not_double_deducted(self):
        """A public holiday covered by a personal span is deducted once, not twice."""
        PublicHoliday.objects.create(
            country=self.holiday_country,
            name="Public",
            day=datetime.date(2024, 4, 3),  # Wednesday
        )
        self._create_personal_holiday(self.WEEK_START, duration=3)  # Mon-Wed

        # Union is Mon, Tue, Wed -> 3 days. Before the fix: 40 - 8 - 40 -> clamped to 0.
        self.assertEqual(self._get_expected_hours(), 16)

    def test_expected_hours__public_holiday_outside_personal_span_still_deducted(self):
        PublicHoliday.objects.create(
            country=self.holiday_country,
            name="Public",
            day=datetime.date(2024, 4, 5),  # Friday
        )
        self._create_personal_holiday(self.WEEK_START, duration=2)  # Mon + Tue

        # Mon, Tue personal + Fri public -> 3 days.
        self.assertEqual(self._get_expected_hours(), 16)
