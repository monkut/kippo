"""Tests for the weekly-effort close/unlock behavior (kippo#33 / T17, T18)."""

import datetime
from typing import TYPE_CHECKING

from accounts.models import KippoUser
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from freezegun import freeze_time
from rest_framework.test import APIClient

if TYPE_CHECKING:
    from django.http import HttpRequest
    from rest_framework.response import Response

from projects.admin import ProjectWeeklyEffortAdmin
from projects.models import (
    ProjectWeeklyEffort,
    ProjectWeeklyEffortUnlock,
    get_weeklyeffort_close_datetime,
    is_weeklyeffort_closed,
)

WEEKLYEFFORT_LIST_URL = "/api/projects/weeklyeffort/"

# The close is MONTHLY: all entries with week_start in April 2024 close together at
# (last Monday of April = 2024-04-29) + 7 days = 2024-05-06, at the org
# weekly_project_time_deadline (12:05 JST default).
WEEK_START = datetime.date(2024, 4, 1)  # MONDAY, first week of the month
MONTH_LAST_WEEK_START = datetime.date(2024, 4, 29)  # MONDAY, last valid entry date of the month
BEFORE_CLOSE = "2024-05-06 03:04:00"  # 12:04 JST
AFTER_CLOSE = "2024-05-06 03:06:00"  # 12:06 JST


class WeeklyEffortCloseTestCaseBase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.project = created["KippoProject"]
        self.user = created["KippoUser"]

        self.superuser = KippoUser(username="adminuser", is_superuser=True, is_staff=True)
        self.superuser.save()

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create_effort(self, week_start: datetime.date = WEEK_START, hours: int = 5) -> ProjectWeeklyEffort:
        return ProjectWeeklyEffort.objects.create(
            week_start=week_start,
            project=self.project,
            user=self.user,
            hours=hours,
            created_by=self.user,
            updated_by=self.user,
        )

    def _create_unlock(self, expires_datetime: datetime.datetime) -> ProjectWeeklyEffortUnlock:
        return ProjectWeeklyEffortUnlock.objects.create(
            organization=self.organization,
            user=self.user,
            week_start=WEEK_START,
            expires_datetime=expires_datetime,
            created_by=self.superuser,
            updated_by=self.superuser,
        )


class WeeklyEffortCloseModelTestCase(WeeklyEffortCloseTestCaseBase):
    def test_close_datetime__is_month_last_monday_plus_offset_at_org_deadline(self):
        expected = datetime.datetime(2024, 5, 6, 12, 5, tzinfo=settings.JST)
        self.assertEqual(get_weeklyeffort_close_datetime(self.organization, WEEK_START), expected)

        effort = self._create_effort()
        self.assertEqual(effort.close_datetime, expected)

    def test_close_datetime__same_for_all_weeks_of_the_month(self):
        """Every week_start within a month closes at the same datetime."""
        expected = datetime.datetime(2024, 5, 6, 12, 5, tzinfo=settings.JST)
        week_start = WEEK_START
        while week_start <= MONTH_LAST_WEEK_START:
            self.assertEqual(get_weeklyeffort_close_datetime(self.organization, week_start), expected, week_start)
            week_start += datetime.timedelta(days=7)
        # the next month's first week closes a month later
        may_close = get_weeklyeffort_close_datetime(self.organization, datetime.date(2024, 5, 6))
        self.assertEqual(may_close, datetime.datetime(2024, 6, 3, 12, 5, tzinfo=settings.JST))

    @freeze_time(BEFORE_CLOSE)
    def test_is_closed__false_before_deadline(self):
        self.assertFalse(self._create_effort().is_closed())

    @freeze_time(AFTER_CLOSE)
    def test_is_closed__true_after_deadline(self):
        self.assertTrue(self._create_effort().is_closed())

    @freeze_time(AFTER_CLOSE)
    def test_is_closed__false_with_active_unlock(self):
        effort = self._create_effort()
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 7, 12, 0, tzinfo=settings.JST))
        self.assertFalse(effort.is_closed())

    @freeze_time(AFTER_CLOSE)
    def test_is_closed__true_with_expired_unlock(self):
        effort = self._create_effort()
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 6, 12, 5, 30, tzinfo=settings.JST))
        self.assertTrue(effort.is_closed())  # frozen 12:06 JST > expiry 12:05:30 JST

    @freeze_time(AFTER_CLOSE)
    def test_is_closed__unlock_for_other_user_does_not_apply(self):
        effort = self._create_effort()
        ProjectWeeklyEffortUnlock.objects.create(
            organization=self.organization,
            user=self.superuser,
            week_start=WEEK_START,
            expires_datetime=datetime.datetime(2024, 5, 7, 12, 0, tzinfo=settings.JST),
            created_by=self.superuser,
            updated_by=self.superuser,
        )
        self.assertTrue(effort.is_closed())

    @freeze_time(AFTER_CLOSE)
    def test_is_weeklyeffort_closed__unlock_for_other_week_does_not_apply(self):
        # same month (same close datetime), different week_start than the unlock
        other_week = WEEK_START + datetime.timedelta(days=7)
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 7, 12, 0, tzinfo=settings.JST))
        self.assertTrue(is_weeklyeffort_closed(self.organization, self.user, other_week))


class WeeklyEffortCloseApiTestCase(WeeklyEffortCloseTestCaseBase):
    def _post_effort(self, week_start: datetime.date = WEEK_START) -> "Response":
        return self.client.post(
            WEEKLYEFFORT_LIST_URL,
            {"week_start": week_start.isoformat(), "project": str(self.project.pk), "hours": 5},
            format="json",
        )

    @freeze_time(BEFORE_CLOSE)
    def test_create__allowed_before_close(self):
        response = self._post_effort()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertFalse(response.json()["is_closed"])

    @freeze_time(AFTER_CLOSE)
    def test_create__blocked_after_close(self):
        response = self._post_effort()
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("week_start", response.json())

    @freeze_time(AFTER_CLOSE)
    def test_create__allowed_after_close_with_active_unlock(self):
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 7, 12, 0, tzinfo=settings.JST))
        response = self._post_effort()
        self.assertEqual(response.status_code, 201, response.content)

    @freeze_time(AFTER_CLOSE)
    def test_create__blocked_after_close_with_expired_unlock(self):
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 6, 12, 5, 30, tzinfo=settings.JST))
        response = self._post_effort()
        self.assertEqual(response.status_code, 400, response.content)

    @freeze_time(AFTER_CLOSE)
    def test_create__superuser_bypasses_close(self):
        self.client.force_authenticate(user=self.superuser)
        response = self.client.post(
            WEEKLYEFFORT_LIST_URL,
            {"week_start": WEEK_START.isoformat(), "project": str(self.project.pk), "user": self.user.pk, "hours": 5},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_update__blocked_after_close(self):
        with freeze_time(BEFORE_CLOSE):
            effort = self._create_effort()
        with freeze_time(AFTER_CLOSE):
            response = self.client.patch(f"{WEEKLYEFFORT_LIST_URL}{effort.pk}/", {"hours": 10}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        effort.refresh_from_db()
        self.assertEqual(effort.hours, 5)

    def test_update__allowed_before_close(self):
        with freeze_time(BEFORE_CLOSE):
            effort = self._create_effort()
            response = self.client.patch(f"{WEEKLYEFFORT_LIST_URL}{effort.pk}/", {"hours": 10}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_update__allowed_after_close_with_active_unlock(self):
        with freeze_time(BEFORE_CLOSE):
            effort = self._create_effort()
        with freeze_time(AFTER_CLOSE):
            self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 7, 12, 0, tzinfo=settings.JST))
            response = self.client.patch(f"{WEEKLYEFFORT_LIST_URL}{effort.pk}/", {"hours": 10}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    @freeze_time(BEFORE_CLOSE)
    def test_update__cannot_move_entry_into_closed_week(self):
        effort = self._create_effort()
        closed_week = WEEK_START - datetime.timedelta(days=14)
        response = self.client.patch(f"{WEEKLYEFFORT_LIST_URL}{effort.pk}/", {"week_start": closed_week.isoformat()}, format="json")
        self.assertEqual(response.status_code, 400, response.content)

    def test_list__is_closed_field(self):
        with freeze_time(BEFORE_CLOSE):
            self._create_effort()
            response = self.client.get(WEEKLYEFFORT_LIST_URL)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["results"][0]["is_closed"])
        with freeze_time(AFTER_CLOSE):
            response = self.client.get(WEEKLYEFFORT_LIST_URL)
            self.assertTrue(response.json()["results"][0]["is_closed"])


class WeeklyEffortCloseAdminTestCase(WeeklyEffortCloseTestCaseBase):
    def _admin(self) -> ProjectWeeklyEffortAdmin:
        from django.contrib import admin as django_admin

        return ProjectWeeklyEffortAdmin(ProjectWeeklyEffort, django_admin.site)

    def _request(self, user: KippoUser) -> "HttpRequest":
        from django.test import RequestFactory

        request = RequestFactory().get("/admin/projects/projectweeklyeffort/")
        request.user = user
        return request

    @freeze_time(AFTER_CLOSE)
    def test_has_change_permission__denied_for_staff_on_closed_entry(self):
        effort = self._create_effort()
        model_admin = self._admin()
        self.assertFalse(model_admin.has_change_permission(self._request(self.user), effort))
        self.assertFalse(model_admin.has_delete_permission(self._request(self.user), effort))

    @freeze_time(AFTER_CLOSE)
    def test_has_change_permission__allowed_for_superuser_on_closed_entry(self):
        effort = self._create_effort()
        model_admin = self._admin()
        self.assertTrue(model_admin.has_change_permission(self._request(self.superuser), effort))

    @freeze_time(BEFORE_CLOSE)
    def test_has_change_permission__allowed_for_staff_before_close(self):
        effort = self._create_effort()
        model_admin = self._admin()
        self.assertTrue(model_admin.has_change_permission(self._request(self.user), effort))
