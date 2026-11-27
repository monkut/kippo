"""Tests for projects.services.progress and the single effort / progress code path built on it."""

import datetime
from http import HTTPStatus

from accounts.models import Country, PublicHoliday
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from ..definitions import ProjectProgressStatus
from ..models import KippoProject, ProjectWeeklyEffort
from ..services.progress import (
    EFFORT_LEVEL_DANGER,
    EFFORT_LEVEL_NEUTRAL,
    EFFORT_LEVEL_SUCCESS,
    EFFORT_LEVEL_WARNING,
    effort_level,
    effort_meter,
    expected_effort_hours,
    public_holidays_by_country,
)

START_DATE = datetime.date(2024, 1, 1)  # Monday
TARGET_DATE = datetime.date(2024, 1, 12)  # Friday, 2 weeks later: 10 weekdays
HOLIDAY = datetime.date(2024, 1, 8)  # Monday of the 2nd week


class ProgressServiceTestCaseBase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project: KippoProject = created["KippoProject"]
        self.country = Country.objects.create(name="Testland", alpha_2="TT", alpha_3="TST", country_code="999", region="Test")
        PublicHoliday.objects.create(country=self.country, day=HOLIDAY, name="Test Holiday")
        self.organization.default_holiday_country = self.country
        self.organization.save()
        # 9 workdays (10 weekdays - 1 holiday) x 8h == 72h budget: 8h expected per workday
        self.project.start_date = START_DATE
        self.project.target_date = TARGET_DATE
        self.project.allocated_staff_days = 9
        self.project.save()

    def _log_effort(self, hours: int, week_start: datetime.date = START_DATE) -> None:
        ProjectWeeklyEffort.objects.create(
            project=self.project, user=self.user, week_start=week_start, hours=hours, created_by=self.user, updated_by=self.user
        )


class ExpectedEffortHoursTestCase(ProgressServiceTestCaseBase):
    def test_expected_hours_by_date(self):
        before_start = START_DATE - datetime.timedelta(days=1)
        first_friday = datetime.date(2024, 1, 5)
        after_target = TARGET_DATE + datetime.timedelta(days=30)
        expected = expected_effort_hours(self.project, [before_start, START_DATE, first_friday, HOLIDAY, TARGET_DATE, after_target])
        self.assertEqual(
            expected,
            {
                before_start: None,  # before the project period
                START_DATE: 8,
                first_friday: 40,
                HOLIDAY: 40,  # a holiday adds no expected effort
                TARGET_DATE: 72,
                after_target: 72,  # the whole budget once the period is over
            },
        )

    def test_matches_model_method_for_each_date(self):
        dates = [START_DATE + datetime.timedelta(days=offset) for offset in range(-3, 20)]
        expected = expected_effort_hours(self.project, dates)
        for date in dates:
            with self.subTest(date=date):
                self.assertEqual(self.project.get_expected_effort(at_date=date), (None, expected[date]))

    def test_many_dates_cost_one_holiday_query(self):
        dates = [START_DATE + datetime.timedelta(days=offset) for offset in range(12)]
        project = KippoProject.objects.select_related("organization__default_holiday_country").get(pk=self.project.pk)
        with self.assertNumQueries(1):
            expected_effort_hours(project, dates)

    def test_precomputed_holidays_cost_no_query(self):
        project = KippoProject.objects.select_related("organization").get(pk=self.project.pk)
        with self.assertNumQueries(0):
            expected = expected_effort_hours(project, [TARGET_DATE], holidays={HOLIDAY})
        self.assertEqual(expected[TARGET_DATE], 72)

    def test_no_budget_returns_none(self):
        self.project.allocated_staff_days = None
        self.project.save()
        self.assertEqual(expected_effort_hours(self.project, [START_DATE]), {START_DATE: None})

    def test_public_holidays_by_country_ignores_missing_country(self):
        with self.assertNumQueries(0):
            self.assertEqual(public_holidays_by_country([None]), {})
        self.assertEqual(public_holidays_by_country([self.country.id, None]), {self.country.id: {HOLIDAY}})


class EffortLevelTestCase(ProgressServiceTestCaseBase):
    def _status(self, current_effort_hours: int | None, expected_hours: int | None = 100) -> ProjectProgressStatus:
        return ProjectProgressStatus(
            project=self.project,
            date=START_DATE,
            current_effort_hours=current_effort_hours,
            expected_effort_hours=expected_hours,
            expected_effort_days=None,
            allocated_effort_hours=200,
        )

    def test_levels(self):
        threshold = settings.PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD
        cases = (
            (None, EFFORT_LEVEL_NEUTRAL),  # no effort logged: nothing to compare
            (90, EFFORT_LEVEL_SUCCESS),
            (100, EFFORT_LEVEL_SUCCESS),  # exactly as expected
            (101, EFFORT_LEVEL_WARNING),
            (100 + threshold, EFFORT_LEVEL_WARNING),  # the threshold itself is not exceeded
            (100 + threshold + 1, EFFORT_LEVEL_DANGER),
        )
        for current_effort_hours, expected_level in cases:
            with self.subTest(current_effort_hours=current_effort_hours):
                self.assertEqual(effort_level(self._status(current_effort_hours)), expected_level)

    def test_meter_is_none_without_expected_effort(self):
        self.assertIsNone(effort_meter(self._status(50, expected_hours=None)))

    def test_meter_fill_is_capped_by_current_effort_when_over_budget(self):
        self.assertEqual(effort_meter(self._status(50))["fill_percentage"], 25.0)  # 50 / 200
        self.assertEqual(effort_meter(self._status(400))["fill_percentage"], 100.0)  # 400 / max(200, 400)
        self.assertEqual(effort_meter(self._status(None))["fill_percentage"], 0)


class ProjectApiProgressTestCase(ProgressServiceTestCaseBase):
    """The list and detail endpoints derive the effort fields from the same code path."""

    EFFORT_FIELDS = ("projectstatus_display", "weekly_effort_users", "survey_users")

    def setUp(self):
        super().setUp()
        self._log_effort(100)  # far over the 72h budget
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _detail(self) -> dict:
        response = self.client.get(f"{settings.URL_PREFIX}/api/projects/{self.project.id}/")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        return response.json()

    def test_projectstatus_display_has_level(self):
        projectstatus_display = self._detail()["projectstatus_display"]
        self.assertEqual(projectstatus_display["level"], EFFORT_LEVEL_DANGER)
        self.assertEqual(projectstatus_display["current_effort_hours"], 100)
        self.assertEqual(projectstatus_display["expected_effort_hours"], 72)

    def test_list_and_detail_return_the_same_effort_fields(self):
        detail = self._detail()
        response = self.client.get(f"{settings.URL_PREFIX}/api/projects/")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        listed = next(project for project in response.json()["results"] if project["id"] == str(self.project.id))
        for field in self.EFFORT_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(listed[field], detail[field])
        self.assertEqual(detail["weekly_effort_users"][0]["hours"], 100)
        self.assertEqual(detail["weekly_effort_users"][0]["percentage"], 100.0)


class ProjectStatusDetailsViewQueryTestCase(ProgressServiceTestCaseBase):
    """The project status page walks the project period once, however many weeks it draws."""

    def _holiday_query_count(self) -> int:
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        session = self.client.session
        session["organization_id"] = str(self.organization.id)
        session.save()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("project_status_details", kwargs={"project_id": self.project.id}))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        return sum(1 for query in queries if "accounts_publicholiday" in query["sql"])

    def test_holiday_queries_do_not_grow_with_project_length(self):
        short_period_queries = self._holiday_query_count()
        self.project.target_date = START_DATE + datetime.timedelta(weeks=40)
        self.project.save()
        self.assertEqual(self._holiday_query_count(), short_period_queries)
