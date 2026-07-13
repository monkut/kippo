"""Tests for the weekly-effort close/unlock behavior (kippo#33 / T17, T18)."""

import datetime
import json
from typing import TYPE_CHECKING
from unittest import mock

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from freezegun import freeze_time
from rest_framework.test import APIClient

if TYPE_CHECKING:
    from django.http import HttpRequest
    from rest_framework.response import Response

    from projects.services.weekly_effort_reminder import WeeklyEffortCloseReminderManager

from projects.admin import ProjectWeeklyEffortAdmin
from projects.models import (
    ProjectWeeklyEffort,
    ProjectWeeklyEffortUnlock,
)

WEEKLYEFFORT_LIST_URL = "/api/projects/weeklyeffort/"

# The close is MONTHLY: all entries with week_start in April 2024 close together.
# Entry for a week starts the FOLLOWING Monday, so the month's final week (week_start =
# last Monday of April = 2024-04-29) becomes enterable 2024-05-06; the close is
# weekly_effort_close_offset_days (default 7, min 7 = at least 1 week to enter) after
# that: 2024-05-13, at the org weekly_project_time_deadline (12:05 JST default).
WEEK_START = datetime.date(2024, 4, 1)  # MONDAY, first week of the month
MONTH_LAST_WEEK_START = datetime.date(2024, 4, 29)  # MONDAY, last week_start of the month
BEFORE_CLOSE = "2024-05-13 03:04:00"  # 12:04 JST
AFTER_CLOSE = "2024-05-13 03:06:00"  # 12:06 JST


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
            reason="test unlock",
            approved_by=self.superuser,
            approved_datetime=datetime.datetime(2024, 5, 1, tzinfo=settings.JST),
            expires_datetime=expires_datetime,
            created_by=self.user,
            updated_by=self.user,
        )


class WeeklyEffortCloseModelTestCase(WeeklyEffortCloseTestCaseBase):
    def test_close_datetime__is_month_last_entry_start_plus_offset_at_org_deadline(self):
        # last Monday 4/29 -> entry starts 5/6 -> + offset 7 days = 5/13
        expected = datetime.datetime(2024, 5, 13, 12, 5, tzinfo=settings.JST)
        self.assertEqual(self.organization.get_weeklyeffort_close_datetime(WEEK_START), expected)

        effort = self._create_effort()
        self.assertEqual(effort.close_datetime, expected)

    def test_close_datetime__offset_configurable_per_organization(self):
        """The offset after the month's last entry-start date comes from
        KippoOrganization.weekly_effort_close_offset_days (minimum 7 = at least 1 week to enter).
        """
        self.organization.weekly_effort_close_offset_days = 14
        self.organization.full_clean(exclude=None)  # min-7 validator accepts 14
        self.organization.save()
        expected = datetime.datetime(2024, 5, 20, 12, 5, tzinfo=settings.JST)  # entry start 5/6 + 14 days
        self.assertEqual(self.organization.get_weeklyeffort_close_datetime(WEEK_START), expected)

    def test_close_offset_days__minimum_one_week_enforced(self):
        """Offsets under 7 days would give users less than a week to enter the final week."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        self.organization.weekly_effort_close_offset_days = 6
        with self.assertRaises(DjangoValidationError):
            self.organization.full_clean()

    def test_close_datetime__same_for_all_weeks_of_the_month(self):
        """Every week_start within a month closes at the same datetime."""
        expected = datetime.datetime(2024, 5, 13, 12, 5, tzinfo=settings.JST)
        week_start = WEEK_START
        while week_start <= MONTH_LAST_WEEK_START:
            self.assertEqual(self.organization.get_weeklyeffort_close_datetime(week_start), expected, week_start)
            week_start += datetime.timedelta(days=7)
        # the next month's first week closes a month later (May: last Monday 5/27 -> entry start 6/3 -> close 6/10)
        may_close = self.organization.get_weeklyeffort_close_datetime(datetime.date(2024, 5, 6))
        self.assertEqual(may_close, datetime.datetime(2024, 6, 10, 12, 5, tzinfo=settings.JST))

    @freeze_time(BEFORE_CLOSE)
    def test_is_closed__false_before_deadline(self):
        self.assertFalse(self._create_effort().is_closed())

    @freeze_time(AFTER_CLOSE)
    def test_is_closed__true_after_deadline(self):
        self.assertTrue(self._create_effort().is_closed())

    @freeze_time(AFTER_CLOSE)
    def test_is_closed__false_with_active_unlock(self):
        effort = self._create_effort()
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 14, 12, 0, tzinfo=settings.JST))
        self.assertFalse(effort.is_closed())

    @freeze_time(AFTER_CLOSE)
    def test_is_closed__true_with_expired_unlock(self):
        effort = self._create_effort()
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 13, 12, 5, 30, tzinfo=settings.JST))
        self.assertTrue(effort.is_closed())  # frozen 12:06 JST > expiry 12:05:30 JST

    @freeze_time(AFTER_CLOSE)
    def test_is_closed__unlock_for_other_user_does_not_apply(self):
        effort = self._create_effort()
        ProjectWeeklyEffortUnlock.objects.create(
            organization=self.organization,
            user=self.superuser,
            week_start=WEEK_START,
            reason="test unlock",
            approved_by=self.superuser,
            approved_datetime=datetime.datetime(2024, 5, 1, tzinfo=settings.JST),
            expires_datetime=datetime.datetime(2024, 5, 14, 12, 0, tzinfo=settings.JST),
            created_by=self.superuser,
            updated_by=self.superuser,
        )
        self.assertTrue(effort.is_closed())

    @freeze_time(AFTER_CLOSE)
    def test_is_weeklyeffort_closed__unlock_for_other_week_does_not_apply(self):
        # same month (same close datetime), different week_start than the unlock
        other_week = WEEK_START + datetime.timedelta(days=7)
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 14, 12, 0, tzinfo=settings.JST))
        self.assertTrue(self.organization.is_weeklyeffort_closed(self.user, other_week))


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
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 14, 12, 0, tzinfo=settings.JST))
        response = self._post_effort()
        self.assertEqual(response.status_code, 201, response.content)

    @freeze_time(AFTER_CLOSE)
    def test_create__blocked_after_close_with_expired_unlock(self):
        self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 13, 12, 5, 30, tzinfo=settings.JST))
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
            self._create_unlock(expires_datetime=datetime.datetime(2024, 5, 14, 12, 0, tzinfo=settings.JST))
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

    @freeze_time(AFTER_CLOSE)
    def test_is_closed__model_and_serializer_agree(self):
        """The per-row model path (is_closed) and the batched serializer path (is_closed field)
        must agree across pending / active / expired unlocks — the dedup guard against drift (#5).
        """
        effort = self._create_effort()

        def serializer_is_closed() -> bool:
            return self.client.get(WEEKLYEFFORT_LIST_URL).json()["results"][0]["is_closed"]

        # pending (unapproved) unlock — must NOT unlock in either path
        unlock = ProjectWeeklyEffortUnlock.objects.create(
            organization=self.organization,
            user=self.user,
            week_start=WEEK_START,
            reason="pending request",
            created_by=self.user,
            updated_by=self.user,
        )
        self.assertTrue(effort.is_closed())
        self.assertEqual(effort.is_closed(), serializer_is_closed())

        # approve with a future relock deadline — both paths now open
        unlock.approve(approved_by=self.superuser, expires_datetime=datetime.datetime(2024, 5, 14, 12, 0, tzinfo=settings.JST))
        self.assertFalse(effort.is_closed())
        self.assertEqual(effort.is_closed(), serializer_is_closed())

        # past relock deadline — both paths closed again
        unlock.expires_datetime = datetime.datetime(2024, 5, 13, 12, 5, 30, tzinfo=settings.JST)
        unlock.save()
        self.assertTrue(effort.is_closed())
        self.assertEqual(effort.is_closed(), serializer_is_closed())


class WeeklyEffortUnlockRequestApiTestCase(WeeklyEffortCloseTestCaseBase):
    """Unlock request → admin approval flow (kippo#33 / #1 + #2)."""

    UNLOCK_URL = "/api/weekly-effort-unlocks/"

    def setUp(self):
        super().setUp()
        # org admin (project manager) who is NOT the requester
        self.org_admin = KippoUser(username="orgadmin")
        self.org_admin.save()
        OrganizationMembership.objects.create(
            user=self.org_admin,
            organization=self.organization,
            is_project_manager=True,
            created_by=self.org_admin,
            updated_by=self.org_admin,
        )

    def _request_unlock(self, week_start: datetime.date = WEEK_START) -> "Response":
        return self.client.post(
            self.UNLOCK_URL,
            {"organization": str(self.organization.pk), "week_start": week_start.isoformat(), "reason": "遅延入力のため"},
            format="json",
        )

    def test_request__creates_pending_unlock(self):
        response = self._request_unlock()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(str(body["user"]), str(self.user.pk))  # requester fixed to self
        self.assertIsNone(body["approved_datetime"])
        self.assertIsNone(body["expires_datetime"])
        self.assertFalse(body["is_active"])

    def test_request__reason_required(self):
        response = self.client.post(
            self.UNLOCK_URL,
            {"organization": str(self.organization.pk), "week_start": WEEK_START.isoformat(), "reason": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("reason", response.json())

    @freeze_time(AFTER_CLOSE)
    def test_pending_request_does_not_unlock_the_week(self):
        self._request_unlock()  # pending, not approved
        # the closed week is still closed for editing
        self.assertTrue(self.organization.is_weeklyeffort_closed(self.user, WEEK_START))

    @freeze_time(AFTER_CLOSE)
    def test_approve__by_superuser_activates_unlock(self):
        unlock_id = self._request_unlock().json()["id"]
        self.client.force_authenticate(user=self.superuser)
        response = self.client.post(f"{self.UNLOCK_URL}{unlock_id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIsNotNone(body["approved_datetime"])
        self.assertTrue(body["is_active"])
        self.assertFalse(self.organization.is_weeklyeffort_closed(self.user, WEEK_START))

    @freeze_time(AFTER_CLOSE)
    def test_approve__by_org_project_manager(self):
        unlock_id = self._request_unlock().json()["id"]
        self.client.force_authenticate(user=self.org_admin)
        response = self.client.post(f"{self.UNLOCK_URL}{unlock_id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["is_active"])

    @freeze_time(AFTER_CLOSE)
    def test_approve__by_non_admin_forbidden(self):
        unlock_id = self._request_unlock().json()["id"]
        other = KippoUser(username="plainmember")
        other.save()
        OrganizationMembership.objects.create(user=other, organization=self.organization, is_developer=True, created_by=other, updated_by=other)
        self.client.force_authenticate(user=other)
        response = self.client.post(f"{self.UNLOCK_URL}{unlock_id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 403, response.content)

    @freeze_time(AFTER_CLOSE)
    def test_approve__self_request_forbidden_for_non_superuser(self):
        # org_admin requests their own unlock, then tries to approve it → blocked
        self.client.force_authenticate(user=self.org_admin)
        unlock_id = self._request_unlock().json()["id"]
        response = self.client.post(f"{self.UNLOCK_URL}{unlock_id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 403, response.content)

    @freeze_time(AFTER_CLOSE)
    def test_approve__honors_explicit_relock_deadline(self):
        unlock_id = self._request_unlock().json()["id"]
        self.client.force_authenticate(user=self.superuser)
        relock = datetime.datetime(2024, 5, 20, 12, 0, tzinfo=settings.JST)
        response = self.client.post(f"{self.UNLOCK_URL}{unlock_id}/approve/", {"expires_datetime": relock.isoformat()}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        unlock = ProjectWeeklyEffortUnlock.objects.get(pk=unlock_id)
        self.assertEqual(unlock.expires_datetime, relock)

    def test_request__duplicate_returns_400_not_500(self):
        # second request for the same (org, user, week) must 400, not raise an IntegrityError 500
        self.assertEqual(self._request_unlock().status_code, 201)
        response = self._request_unlock()
        self.assertEqual(response.status_code, 400, response.content)

    @freeze_time(AFTER_CLOSE)
    def test_approve__naive_expires_datetime_assumed_jst(self):
        # a tz-naive expires_datetime must not 500 (aware/naive compare); it is assumed JST
        unlock_id = self._request_unlock().json()["id"]
        self.client.force_authenticate(user=self.superuser)
        response = self.client.post(f"{self.UNLOCK_URL}{unlock_id}/approve/", {"expires_datetime": "2024-05-20T12:00:00"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["is_active"])
        unlock = ProjectWeeklyEffortUnlock.objects.get(pk=unlock_id)
        self.assertEqual(unlock.expires_datetime, datetime.datetime(2024, 5, 20, 12, 0, tzinfo=settings.JST))

    @freeze_time(AFTER_CLOSE)
    def test_approve__past_relock_deadline_rejected(self):
        unlock_id = self._request_unlock().json()["id"]
        self.client.force_authenticate(user=self.superuser)
        response = self.client.post(f"{self.UNLOCK_URL}{unlock_id}/approve/", {"expires_datetime": "2024-05-01T12:00:00+09:00"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)

    def test_list__org_scoped_for_non_superuser(self):
        # a user from another org cannot see this org's unlock requests
        self._request_unlock()
        other_org_user = KippoUser(username="otherorguser")
        other_org_user.save()
        self.client.force_authenticate(user=other_org_user)
        response = self.client.get(self.UNLOCK_URL)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["count"], 0)


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


class WeeklyEffortCloseReminderTestCase(WeeklyEffortCloseTestCaseBase):
    """24h pre-close Slack reminder (kippo#33 / #3)."""

    # 13:00 JST on 2024-05-12 = ~23h before the April-2024 close (2024-05-13 12:05 JST)
    WITHIN_WINDOW = "2024-05-12 04:00:00"
    OUTSIDE_WINDOW = "2024-05-01 04:00:00"  # 12 days before the close

    def setUp(self):
        super().setUp()
        self.organization.slack_api_token = "xoxb-test-token"  # noqa: S105
        self.organization.slack_channel_name = "#kippo"
        self.organization.save()
        self.membership = OrganizationMembership.objects.get(organization=self.organization, user=self.user)
        self.membership.slack_user_id = "U12345678"
        self.membership.save()

    def _manager(self) -> "WeeklyEffortCloseReminderManager":
        from projects.services.weekly_effort_reminder import WeeklyEffortCloseReminderManager

        with mock.patch("projects.services.weekly_effort_reminder.WebClient") as mock_client_cls:
            manager = WeeklyEffortCloseReminderManager(self.organization)
            manager._mock_client = mock_client_cls.return_value
        return manager

    @freeze_time(WITHIN_WINDOW)
    def test_get_upcoming_close__within_window(self):
        upcoming = self._manager().get_upcoming_close()
        self.assertIsNotNone(upcoming)
        close_dt, month_first = upcoming
        self.assertEqual(month_first, datetime.date(2024, 4, 1))
        self.assertEqual(close_dt, datetime.datetime(2024, 5, 13, 12, 5, tzinfo=settings.JST))

    @freeze_time(OUTSIDE_WINDOW)
    def test_get_upcoming_close__outside_window(self):
        self.assertIsNone(self._manager().get_upcoming_close())

    @freeze_time(WITHIN_WINDOW)
    def test_get_missing_by_member__lists_per_week_gaps(self):
        manager = self._manager()
        # self.user (developer) has no effort -> all 5 April Mondays missing
        missing_map = {m.user_id: weeks for m, weeks in manager.get_missing_by_member(datetime.date(2024, 4, 1))}
        self.assertIn(self.user.pk, missing_map)
        self.assertEqual(
            missing_map[self.user.pk],
            [datetime.date(2024, 4, d) for d in (1, 8, 15, 22, 29)],
        )
        # logging one week removes it from the gaps
        self._create_effort(week_start=datetime.date(2024, 4, 8))
        missing_map = {m.user_id: weeks for m, weeks in manager.get_missing_by_member(datetime.date(2024, 4, 1))}
        self.assertNotIn(datetime.date(2024, 4, 8), missing_map[self.user.pk])

    @freeze_time(WITHIN_WINDOW)
    def test_get_missing_by_member__excludes_unassigned_user(self):
        # the org's auto-created (unassigned) sentinel is is_developer=True but must not be reminded
        unassigned = self.organization.get_unassigned_kippouser()
        user_ids = {m.user_id for m, _ in self._manager().get_missing_by_member(datetime.date(2024, 4, 1))}
        self.assertIn(self.user.pk, user_ids)
        self.assertNotIn(unassigned.pk, user_ids)

    @freeze_time(WITHIN_WINDOW)
    def test_post__mentions_missing_member_in_org_channel(self):
        manager = self._manager()
        blocks = manager.post()
        self.assertIsNotNone(blocks)
        manager._mock_client.chat_postMessage.assert_called_once()
        _, kwargs = manager._mock_client.chat_postMessage.call_args
        self.assertEqual(kwargs["channel"], "#kippo")
        text = json.dumps(blocks, ensure_ascii=False)
        self.assertIn("<@U12345678>", text)  # @-mention via slack_user_id
        self.assertIn("4月1日週", text)  # per-week gap listed
        self.assertIn(f"{settings.HOST_URL}{settings.URL_PREFIX}/ui/weekly-effort", text)  # entry-UI deep link

    @freeze_time(OUTSIDE_WINDOW)
    def test_post__no_post_outside_window(self):
        manager = self._manager()
        self.assertIsNone(manager.post())
        manager._mock_client.chat_postMessage.assert_not_called()
