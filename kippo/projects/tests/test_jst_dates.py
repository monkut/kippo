"""'Today' is the JST date, not the UTC date.

Each test freezes the clock in the first nine hours of a JST day, when the UTC date is still the
previous day. A date taken from `timezone.now().date()` or `<aware datetime>.date()` is then one day
behind, which these tests would catch.
"""

import datetime

from accounts.models import OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from freezegun import freeze_time
from rest_framework.test import APIClient

from projects.admin import KippoProjectAdmin
from projects.functions import current_week_startdate
from projects.models import KippoMilestone, KippoProject, KippoProjectStatus
from projects.models.project import get_target_date_default
from projects.models.survey import get_current_month

# Monday 2026-03-02 08:30 JST == Sunday 2026-03-01 23:30 UTC
MONDAY_MORNING_JST = "2026-03-01 23:30:00"
JST_TODAY = datetime.date(2026, 3, 2)
UTC_TODAY = datetime.date(2026, 3, 1)
# Wednesday 2026-04-01 08:30 JST == Tuesday 2026-03-31 23:30 UTC
FIRST_OF_MONTH_MORNING_JST = "2026-03-31 23:30:00"


@freeze_time(MONDAY_MORNING_JST)
class JstTodayTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project: KippoProject = created["KippoProject"]

    def test_frozen_instant_straddles_the_date_line(self):
        self.assertEqual(timezone.now().date(), UTC_TODAY)
        self.assertEqual(timezone.localdate(), JST_TODAY)

    def test_current_week_starts_on_this_monday(self):
        self.assertEqual(current_week_startdate(), JST_TODAY)

    def test_target_date_default_counts_from_today(self):
        self.assertEqual(get_target_date_default(), JST_TODAY + datetime.timedelta(days=settings.DEFAULT_KIPPORPOJECT_TARGET_DATE_DAYS))

    def test_project_actual_date_of_today_is_not_in_the_future(self):
        self.project.actual_date = JST_TODAY
        self.project.clean()  # must not raise
        self.project.actual_date = JST_TODAY + datetime.timedelta(days=1)
        with self.assertRaises(ValidationError):
            self.project.clean()

    def test_milestone_due_yesterday_is_delayed(self):
        milestone = KippoMilestone(project=self.project, title="m", target_date=UTC_TODAY)
        self.assertTrue(milestone.is_delayed)
        self.assertFalse(KippoMilestone(project=self.project, title="m", target_date=JST_TODAY).is_delayed)

    def test_milestone_actual_date_of_today_is_not_in_the_future(self):
        KippoMilestone(project=self.project, title="m", actual_date=JST_TODAY).clean()  # must not raise

    def test_active_milestones_exclude_one_due_yesterday(self):
        KippoMilestone.objects.bulk_create(
            [
                KippoMilestone(project=self.project, title="due yesterday", number=0, target_date=UTC_TODAY),
                KippoMilestone(project=self.project, title="due today", number=1, target_date=JST_TODAY),
            ]
        )
        self.assertEqual([milestone.title for milestone in self.project.active_milestones()], ["due today"])

    def test_latest_status_comment_shows_the_jst_date(self):
        KippoProjectStatus.objects.create(project=self.project, comment="status", created_by=self.user, updated_by=self.user)
        admin = KippoProjectAdmin(model=KippoProject, admin_site=None)
        self.assertIn("(03/02) ", admin.get_latest_kippoprojectstatus_comment(self.project))


@freeze_time(FIRST_OF_MONTH_MORNING_JST)
class JstMonthTestCase(TestCase):
    def test_current_month_is_the_new_month(self):
        self.assertEqual(get_current_month(), datetime.date(2026, 4, 1))


@freeze_time(MONDAY_MORNING_JST)
class MissingWeeksJstTestCase(TestCase):
    """GET /api/weekly-effort/missing-weeks/ on a JST Monday morning."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.organization.fiscalyear_start_month = 2  # fiscal year began 2026-02-01
        self.organization.save()
        self.user = created["KippoUser"]
        membership = OrganizationMembership.objects.get(user=self.user, organization=self.organization)
        for weekday in ("monday", "tuesday", "wednesday", "thursday", "friday"):
            setattr(membership, weekday, True)
        membership.save()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_last_week_is_missing_and_this_week_is_not_yet_due(self):
        response = self.client.get("/api/weekly-effort/missing-weeks/")
        data = response.json()
        self.assertEqual(data["fiscal_year_start"], "2026-02-01")
        # Last week (Monday 02-23) has ended and has no entry. Under the UTC date it was taken for
        # the current week and dropped from the list.
        self.assertIn("2026-02-23", data["missing_weeks"])
        self.assertNotIn("2026-03-02", data["missing_weeks"])
