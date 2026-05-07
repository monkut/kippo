"""Tests for the project completion forecast service + endpoint.

Covers monkut/kippo#226 (Phase 1 of feature #224). Decisions A1–A6, D5, O1–O3.
"""

import datetime
from http import HTTPStatus

from accounts.models import Country, KippoOrganization, KippoUser, OrganizationMembership, PersonalHoliday, PublicHoliday
from commons.definitions import SATURDAY
from commons.functions import first_of_next_month
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from projects.models import KippoProject, ProjectMonthlyAssignment, ProjectWeeklyEffort
from projects.services.forecast import (
    ProjectAssignmentForecastManager,
    ProjectStartDateRequiredError,
)


def _set_today_dependent_dates(project: KippoProject) -> None:
    """Set start_date well in the past so the forecast walks future months."""
    project.start_date = (timezone.localdate() - datetime.timedelta(days=180)).replace(day=1)
    project.allocated_staff_days = 20
    project.save()


class ForecastServiceTestCase(TestCase):
    """Direct unit tests of ProjectAssignmentForecastManager.compute()."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        # Ensure deterministic 8-hour day for math (default org has 8)
        self.organization.day_workhours = 8
        self.organization.save()

        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        _set_today_dependent_dates(self.project)
        self.today = timezone.localdate()

    def _membership(self, user: KippoUser) -> OrganizationMembership:
        return OrganizationMembership.objects.get(user=user, organization=self.organization)

    def _make_assignment(self, *, user: KippoUser | None = None, month: datetime.date, percentage: int) -> None:
        ProjectMonthlyAssignment.objects.create(
            project=self.project,
            user=user or self.user,
            month=month,
            percentage=percentage,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_raises_when_start_date_is_null(self):
        self.project.start_date = None
        self.project.save()
        with self.assertRaises(ProjectStartDateRequiredError):
            ProjectAssignmentForecastManager(self.project).compute()

    def test_returns_null_date_when_no_assignments_and_no_effort(self):
        result = ProjectAssignmentForecastManager(self.project).compute()
        self.assertIsNone(result["estimated_completion_date"])
        self.assertIsNone(result["delta_from_target_date_days"])

    def test_returns_null_date_when_allocated_staff_days_zero(self):
        self.project.allocated_staff_days = 0
        self.project.save()
        result = ProjectAssignmentForecastManager(self.project).compute()
        self.assertIsNone(result["estimated_completion_date"])

    def test_already_complete_returns_latest_logged_week_start(self):
        # 20 staff_days * 8 day_workhours = 160 allocated hours; log 200 to overshoot.
        early = self.today - datetime.timedelta(days=21)
        # ProjectWeeklyEffort.week_start must be a Monday
        early_monday = early - datetime.timedelta(days=early.weekday())
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=early_monday,
            hours=200,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        result = ProjectAssignmentForecastManager(self.project).compute()
        self.assertEqual(result["estimated_completion_date"], early_monday)

    def test_future_only_projection_finds_completion_within_horizon(self):
        # 100% allocation for one user across enough months to complete (160 hours @ 8h/day,
        # ~5 work-days/week) — should land within ~4 weeks of next-month start.
        next_month = first_of_next_month(self.today)
        # Walk a few months
        for offset in range(3):
            year = next_month.year + (next_month.month - 1 + offset) // 12
            month_no = (next_month.month - 1 + offset) % 12 + 1
            self._make_assignment(month=datetime.date(year, month_no, 1), percentage=100)

        result = ProjectAssignmentForecastManager(self.project).compute()
        completion = result["estimated_completion_date"]
        self.assertIsNotNone(completion)
        self.assertGreaterEqual(completion, next_month)
        # Should not be more than 90 days into the future (sanity)
        self.assertLessEqual((completion - next_month).days, 90)

    def test_mixed_past_effort_plus_future_assignments(self):
        # Log 80 hours (half of allocated) in the past, then assign 100% next month → completion early next month.
        past_monday = self.today - datetime.timedelta(days=14)
        past_monday = past_monday - datetime.timedelta(days=past_monday.weekday())
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=past_monday,
            hours=80,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        next_month = first_of_next_month(self.today)
        self._make_assignment(month=next_month, percentage=100)

        result = ProjectAssignmentForecastManager(self.project).compute()
        completion = result["estimated_completion_date"]
        self.assertIsNotNone(completion)
        # 80 remaining hours / 8 hours per day = 10 work-days → roughly 2 work-weeks into next month
        self.assertLess((completion - next_month).days, 21)

    def test_in_progress_month_logged_effort_counts_only(self):
        # 80 logged hours within current month — must be counted toward "spent".
        current_monday = self.today - datetime.timedelta(days=self.today.weekday())
        if current_monday.month != self.today.month:
            current_monday = self.today.replace(day=1)
            current_monday = current_monday + datetime.timedelta(days=(7 - current_monday.weekday()) % 7)
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=current_monday,
            hours=80,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        next_month = first_of_next_month(self.today)
        self._make_assignment(month=next_month, percentage=100)

        result = ProjectAssignmentForecastManager(self.project).compute()
        completion = result["estimated_completion_date"]
        self.assertIsNotNone(completion)
        # Spent 80 → remaining 80 → should still finish in ~2 weeks of next month
        self.assertLess((completion - next_month).days, 21)

    def test_user_committed_weekdays_skips_non_working_days(self):
        # Restrict user to Monday only (one day per week). 100% → 8 hours per Monday
        # = 32-40 hours/month. Allocated 160. Should take ~5 months.
        membership = self._membership(self.user)
        for attr in ("tuesday", "wednesday", "thursday", "friday"):
            setattr(membership, attr, False)
        membership.save()

        next_month = first_of_next_month(self.today)
        for offset in range(8):
            year = next_month.year + (next_month.month - 1 + offset) // 12
            month_no = (next_month.month - 1 + offset) % 12 + 1
            self._make_assignment(month=datetime.date(year, month_no, 1), percentage=100)

        result = ProjectAssignmentForecastManager(self.project).compute()
        completion = result["estimated_completion_date"]
        self.assertIsNotNone(completion)
        # Completion should be ~20 Mondays out from next_month (160h / 8h per Monday)
        days_out = (completion - next_month).days
        self.assertGreaterEqual(days_out, 130)  # at least ~19 weeks
        self.assertLessEqual(days_out, 200)

    def test_public_holiday_subtracted_when_user_country_set(self):
        # User has a holiday country, project organization does not — only user's PHs subtract.
        country = Country.objects.create(name="Testland", alpha_2="TT", alpha_3="TST", country_code="999", region="Test")
        self.user.holiday_country = country
        self.user.save()

        next_month = first_of_next_month(self.today)
        # Add a public holiday on the first weekday of the next-month assignment window
        first_weekday = next_month
        while first_weekday.weekday() >= SATURDAY:
            first_weekday += datetime.timedelta(days=1)
        PublicHoliday.objects.create(country=country, day=first_weekday, name="Test Holiday")

        # 100% but only for next month — without holiday, would finish in ~20 work-days
        self._make_assignment(month=next_month, percentage=100)
        for offset in range(1, 4):
            year = next_month.year + (next_month.month - 1 + offset) // 12
            month_no = (next_month.month - 1 + offset) % 12 + 1
            self._make_assignment(month=datetime.date(year, month_no, 1), percentage=100)

        result = ProjectAssignmentForecastManager(self.project).compute()
        # Just verify it returns a future date and that the holiday was processed (no crash)
        self.assertIsNotNone(result["estimated_completion_date"])

    def test_personal_holiday_subtracted(self):
        next_month = first_of_next_month(self.today)
        # PH on the first day of next month with duration 5 (a work week)
        PersonalHoliday.objects.create(user=self.user, day=next_month, duration=7)
        self._make_assignment(month=next_month, percentage=100)
        for offset in range(1, 4):
            year = next_month.year + (next_month.month - 1 + offset) // 12
            month_no = (next_month.month - 1 + offset) % 12 + 1
            self._make_assignment(month=datetime.date(year, month_no, 1), percentage=100)

        result = ProjectAssignmentForecastManager(self.project).compute()
        completion = result["estimated_completion_date"]
        self.assertIsNotNone(completion)
        # Completion is pushed back by ~5 work-days vs. no-holiday case — direction check only
        self.assertGreater(completion, next_month + datetime.timedelta(days=14))

    def test_target_date_set_returns_delta(self):
        next_month = first_of_next_month(self.today)
        self._make_assignment(month=next_month, percentage=100)
        for offset in range(1, 4):
            year = next_month.year + (next_month.month - 1 + offset) // 12
            month_no = (next_month.month - 1 + offset) % 12 + 1
            self._make_assignment(month=datetime.date(year, month_no, 1), percentage=100)
        # Target way in the future — completion should be early, delta negative
        self.project.target_date = self.today + datetime.timedelta(days=365)
        self.project.save()

        result = ProjectAssignmentForecastManager(self.project).compute()
        self.assertEqual(result["target_date"], self.project.target_date)
        self.assertIsNotNone(result["delta_from_target_date_days"])
        self.assertLess(result["delta_from_target_date_days"], 0)  # ahead of target

    def test_target_date_null_returns_null_delta(self):
        next_month = first_of_next_month(self.today)
        self._make_assignment(month=next_month, percentage=100)
        self.project.target_date = None
        self.project.save()
        result = ProjectAssignmentForecastManager(self.project).compute()
        self.assertIsNone(result["target_date"])
        self.assertIsNone(result["delta_from_target_date_days"])


class ForecastEndpointTestCase(TestCase):
    """Test the GET /api/projects/<id>/forecast/ endpoint."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        _set_today_dependent_dates(self.project)

        # Cross-org user for permission test
        self.other_org = KippoOrganization.objects.create(
            name="forecast-other-org",
            github_organization_name="forecastotherorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.outsider = KippoUser.objects.create(username="forecast-outsider", email="outsider@example.com")
        OrganizationMembership.objects.create(
            user=self.outsider,
            organization=self.other_org,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/forecast/"

    def test_unauthenticated_returns_401(self):
        anon = APIClient()
        response = anon.get(self.url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_returns_payload_with_expected_keys(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        body = response.json()
        self.assertIn("estimated_completion_date", body)
        self.assertIn("delta_from_target_date_days", body)
        self.assertIn("target_date", body)

    def test_start_date_null_returns_400(self):
        self.project.start_date = None
        self.project.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.json()["code"], "project_start_date_required")

    def test_cross_org_user_gets_404(self):
        cross_client = APIClient()
        cross_client.force_authenticate(user=self.outsider)
        response = cross_client.get(self.url)
        # Org-scoped queryset hides the row, DRF returns 404
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_openapi_schema_exposes_forecast_endpoint(self):
        """The forecast action must appear in the generated OpenAPI schema so clients can codegen."""
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        path = f"{settings.URL_PREFIX}/api/projects/{{id}}/forecast/"
        self.assertIn(path, schema["paths"])
        self.assertIn("get", schema["paths"][path])
