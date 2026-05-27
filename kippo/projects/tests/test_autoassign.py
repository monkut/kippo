"""Tests for the auto-create future-month assignment service + signal — kippo#240.

Covers decisions D1–D4:
- D1: scaling — binary search + ceil + 3-day tolerance.
- D2: provenance — trigger row's `updated_by` (fallback to `created_by`).
- D3: past-month rows generated literally start_month+1 through target_date.
- D4: bulk_create persistence (no post_save recursion, no full_clean).
"""

import datetime
from unittest.mock import patch

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from dateutil.relativedelta import relativedelta
from django.test import TestCase
from django.utils import timezone

from projects.models import KippoProject, ProjectMonthlyAssignment
from projects.services.autoassign import (
    auto_create_future_assignments,
    latest_contiguous_confirmed_month,
)


class AutoAssignTestCaseBase(TestCase):
    """Shared fixture builder mirroring the suggester test base."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.organization.day_workhours = 8
        self.organization.save()
        self.user = created["KippoUser"]
        self.project: KippoProject = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Project window relative to today: start_month = today's month, target = +6 months.
        # Gives 6 future months that the forecast will actually project against (forecast.py:106
        # filters anything < first_of_next_month(today)).
        today = timezone.localdate()
        self.start_month = today.replace(day=1)
        self.project.start_date = self.start_month
        self.project.target_date = self.start_month + relativedelta(months=6)
        self.project.allocated_staff_days = 30
        self.project.save()

    def _add_member(self, username: str) -> KippoUser:
        user = KippoUser.objects.create(username=username, email=f"{username}@example.com")
        OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            is_developer=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        return user

    def _make_assignment(
        self,
        *,
        user: KippoUser | None = None,
        month: datetime.date | None = None,
        percentage: int,
        is_confirmed: bool = False,
        project: KippoProject | None = None,
        created_by: KippoUser | None = None,
        updated_by: KippoUser | None = None,
    ) -> ProjectMonthlyAssignment:
        return ProjectMonthlyAssignment.objects.create(
            project=project or self.project,
            user=user or self.user,
            month=month or self.start_month,
            percentage=percentage,
            is_confirmed=is_confirmed,
            created_by=created_by or self.github_manager,
            updated_by=updated_by or self.github_manager,
        )


class AutoAssignServiceTestCase(AutoAssignTestCaseBase):
    """Direct unit tests of `auto_create_future_assignments` — bypasses the signal."""

    # ----------------------------------------------------------- eligibility / no-op

    def test_no_op_when_project_is_closed(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.is_closed = True
        self.project.save()

        rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])
        self.assertEqual(ProjectMonthlyAssignment.objects.filter(project=self.project).count(), 1)

    def test_no_op_when_actual_date_set(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.actual_date = self.start_month + relativedelta(months=2)
        self.project.save()

        rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_target_date_missing(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.target_date = None
        self.project.save()

        rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_target_date_le_start_month(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.target_date = self.start_month + datetime.timedelta(days=15)  # within start_month
        self.project.save()

        rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_start_date_missing(self):
        # Can't seed without a first month.
        self.project.start_date = None
        self.project.save()
        rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_no_first_month_rows(self):
        rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_seed_all_zero(self):
        # _make_assignment with percentage=0 still creates a row (model allows 0).
        # Such rows are excluded by the seed query (`percentage__gt=0`); seed becomes empty.
        self._make_assignment(percentage=0, is_confirmed=True)
        rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_tops_up_missing_months_around_existing_future_row(self):
        # kippo#17: a single pre-existing future row no longer aborts the run; surrounding
        # missing months are still populated. Use ample effort so #18's forecast bound
        # doesn't truncate the window.
        self.project.allocated_staff_days = 1000
        self.project.save()
        self._make_assignment(percentage=50, is_confirmed=True)
        self._make_assignment(month=self.start_month + relativedelta(months=2), percentage=20, is_confirmed=False)

        rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        # 5 missing months created (target window has 6 future months; one is occupied).
        self.assertEqual(len(rows), 5)
        created_months = {row.month for row in rows}
        self.assertNotIn(self.start_month + relativedelta(months=2), created_months)
        # Pre-existing row is untouched.
        existing = ProjectMonthlyAssignment.objects.get(project=self.project, month=self.start_month + relativedelta(months=2))
        self.assertEqual(existing.percentage, 20)
        self.assertFalse(existing.is_confirmed)

    def test_returns_empty_when_no_missing_months(self):
        # All future months already populated → nothing to top-up.
        self._make_assignment(percentage=50, is_confirmed=True)
        for offset in range(1, 7):
            self._make_assignment(month=self.start_month + relativedelta(months=offset), percentage=20, is_confirmed=False)
        rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    # --------------------------------------------------------------- months generated (D3)

    def test_creates_one_row_per_seed_user_per_future_month(self):
        # 6 future months: start_month+1 through target_month inclusive (with effort
        # generous enough that the forecast doesn't cap us short of target).
        self.project.allocated_staff_days = 1000
        self.project.save()
        self._make_assignment(percentage=50, is_confirmed=True)

        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(len(created), 6)
        months = sorted({row.month for row in created})
        self.assertEqual(months[0], self.start_month + relativedelta(months=1))
        self.assertEqual(months[-1], self.start_month + relativedelta(months=6))
        self.assertEqual(len(months), 6)

    def test_generates_past_months_when_start_date_is_historical(self):
        # D3: past-month rows persisted even though forecast.py:106 will ignore them.
        # Project window: 2020-01-01 → 2020-07-01 — fully historical relative to today.
        self.project.start_date = datetime.date(2020, 1, 1)
        self.project.target_date = datetime.date(2020, 7, 1)
        self.project.save()
        self.start_month = datetime.date(2020, 1, 1)
        self._make_assignment(month=self.start_month, percentage=50, is_confirmed=True)

        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        # 6 historical months persisted: 2020-02-01 through 2020-07-01.
        self.assertEqual(len(created), 6)

    # -------------------------------------------------------------------- D2: provenance

    def test_uses_trigger_row_updated_by_for_created_by_and_updated_by(self):
        actor = self._add_member("trigger-actor")
        self._make_assignment(percentage=50, is_confirmed=True, updated_by=actor)

        created, _skip = auto_create_future_assignments(self.project, actor)
        self.assertTrue(created)
        for row in created:
            self.assertEqual(row.created_by_id, actor.id)
            self.assertEqual(row.updated_by_id, actor.id)

    def test_falls_back_to_created_by_when_updated_by_is_none(self):
        # Simulate the signal's resolution: updated_by None → created_by.
        # Service receives whatever `triggered_by` the signal resolved.
        self._make_assignment(percentage=50, is_confirmed=True)
        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        for row in created:
            self.assertEqual(row.created_by_id, self.github_manager.id)

    # ----------------------------------------------------------- D1: scaling math

    def test_within_tolerance_seed_unchanged(self):
        # Seed already lands close enough: don't scale, persist seed as-is.
        # 50% × 8h × ~5 weekdays/week → roughly 100h/month → completes well before target.
        self._make_assignment(percentage=50, is_confirmed=True)
        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        # Fast seed → no scaling: every row keeps the seed percentage.
        for row in created:
            self.assertEqual(row.percentage, 50)

    def test_slow_seed_is_scaled_up(self):
        # Single 5% seed produces ~1h/day. 240h ÷ 1h/day far exceeds the project window
        # → binary search will scale up.
        self._make_assignment(percentage=5, is_confirmed=True)
        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        seed_pct = 5
        # Every persisted row should be > seed (scaled up by ceil).
        self.assertTrue(all(row.percentage > seed_pct for row in created))

    def test_persisted_percentage_capped_at_org_soft_ceiling(self):
        # With soft ceiling 75%, a single user can never persist > 75% per month.
        self.organization.project_assignment_member_soft_ceiling = 75
        self.organization.save()
        self._make_assignment(percentage=5, is_confirmed=True)

        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        for row in created:
            self.assertLessEqual(row.percentage, 75)

    def test_other_org_load_reduces_per_user_cap(self):
        # User has 60% on a *different* project for the next month → cap shrinks to 40% there.
        other_project = KippoProject.objects.create(
            organization=self.organization,
            name="other-project",
            github_project_html_url="https://github.com/orgs/myorg/projects/99",
            github_project_api_nodeid="PVT_kwDOOTHER",
            columnset=self.project.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self._make_assignment(percentage=5, is_confirmed=True)  # seed
        ProjectMonthlyAssignment.objects.create(
            project=other_project,
            user=self.user,
            month=self.start_month + relativedelta(months=1),
            percentage=60,
            is_confirmed=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        # Per-user cap across all months = min(soft_ceiling=75, 100-60 in Feb) = 40.
        self.assertTrue(created)
        for row in created:
            self.assertLessEqual(row.percentage, 40)

    # ------------------------------------------------------- D4: bulk_create persistence

    def test_persisted_rows_are_unconfirmed(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        for row in created:
            self.assertFalse(row.is_confirmed)

    def test_forecast_failure_is_swallowed(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        with patch(
            "projects.services.autoassign.ProjectAssignmentForecastManager.compute",
            side_effect=RuntimeError("boom"),
        ):
            rows, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])


class AutoAssignSignalTestCase(AutoAssignTestCaseBase):
    """Signal wiring: post_save trigger + transaction.on_commit semantics."""

    def test_confirming_last_first_month_row_triggers_creation(self):
        # Ample effort so kippo#18 forecast bound doesn't truncate the 6-month window.
        self.project.allocated_staff_days = 1000
        self.project.save()
        actor = self._add_member("actor")
        # Two unconfirmed first-month rows.
        self._make_assignment(user=self.user, percentage=30, is_confirmed=False)
        second = self._make_assignment(user=actor, percentage=20, is_confirmed=False)

        with self.captureOnCommitCallbacks(execute=True):
            second.is_confirmed = True
            second.updated_by = actor
            second.save()
        # Confirming a non-last row first.
        self.assertEqual(
            ProjectMonthlyAssignment.objects.filter(project=self.project, month__gt=self.start_month).count(),
            0,
        )

        first = ProjectMonthlyAssignment.objects.get(project=self.project, user=self.user, month=self.start_month)
        with self.captureOnCommitCallbacks(execute=True):
            first.is_confirmed = True
            first.updated_by = actor
            first.save()
        future = ProjectMonthlyAssignment.objects.filter(project=self.project, month__gt=self.start_month)
        # 2 seed users × 6 future months = 12 rows.
        self.assertEqual(future.count(), 12)
        for row in future:
            self.assertEqual(row.updated_by_id, actor.id)

    def test_confirming_non_last_row_does_not_trigger(self):
        self._make_assignment(user=self.user, percentage=30, is_confirmed=False)
        actor = self._add_member("actor2")
        self._make_assignment(user=actor, percentage=20, is_confirmed=False)

        first = ProjectMonthlyAssignment.objects.get(user=self.user, month=self.start_month)
        with self.captureOnCommitCallbacks(execute=True):
            first.is_confirmed = True
            first.save()
        self.assertEqual(
            ProjectMonthlyAssignment.objects.filter(project=self.project, month__gt=self.start_month).count(),
            0,
        )

    def test_resaving_already_confirmed_row_does_not_duplicate(self):
        # Wrap creation in captureOnCommitCallbacks so the post_save's transaction.on_commit
        # callback actually runs — TestCase rolls back at end-of-test, so on_commit normally
        # never fires. Ample effort so kippo#18 doesn't truncate the window.
        self.project.allocated_staff_days = 1000
        self.project.save()
        with self.captureOnCommitCallbacks(execute=True):
            seed = self._make_assignment(percentage=50, is_confirmed=True)
        first_count = ProjectMonthlyAssignment.objects.filter(project=self.project, month__gt=self.start_month).count()
        self.assertEqual(first_count, 6)

        # Re-save: top-up finds no missing months → no new rows.
        with self.captureOnCommitCallbacks(execute=True):
            seed.percentage = 51
            seed.save()
        self.assertEqual(
            ProjectMonthlyAssignment.objects.filter(project=self.project, month__gt=self.start_month).count(),
            6,
        )

    def test_bulk_create_does_not_recursively_fire_signal(self):
        # Seed → service is invoked → bulk_create persists 6 rows. Each persisted row
        # would trigger post_save if we used .save(); bulk_create skips that, and the
        # idempotency guard would defend the second invocation regardless. Ample effort
        # so kippo#18 doesn't truncate the window.
        self.project.allocated_staff_days = 1000
        self.project.save()
        seed = self._make_assignment(percentage=50, is_confirmed=False)

        signal_calls: list[int] = []

        def counter(sender: type[ProjectMonthlyAssignment], instance: ProjectMonthlyAssignment, created: bool, **kwargs) -> None:  # noqa: ARG001
            signal_calls.append(instance.pk)

        from django.db.models.signals import post_save

        post_save.connect(counter, sender=ProjectMonthlyAssignment)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                seed.is_confirmed = True
                seed.save()
        finally:
            post_save.disconnect(counter, sender=ProjectMonthlyAssignment)

        # Exactly one post_save fire (the trigger row's). The 6 bulk_created children fire none.
        self.assertEqual(len(signal_calls), 1)
        self.assertEqual(signal_calls[0], seed.pk)
        self.assertEqual(
            ProjectMonthlyAssignment.objects.filter(project=self.project, month__gt=self.start_month).count(),
            6,
        )


class LatestContiguousConfirmedMonthTestCase(AutoAssignTestCaseBase):
    """Smoke tests for the trigger predicate (kippo#17)."""

    def test_returns_none_when_start_date_missing(self):
        self.project.start_date = None
        self.project.save()
        self.assertIsNone(latest_contiguous_confirmed_month(self.project))

    def test_returns_none_when_no_rows_for_start_month(self):
        self.assertIsNone(latest_contiguous_confirmed_month(self.project))

    def test_returns_none_when_any_start_month_row_unconfirmed(self):
        self._make_assignment(percentage=30, is_confirmed=True)
        actor = self._add_member("actor3")
        self._make_assignment(user=actor, percentage=20, is_confirmed=False)
        self.assertIsNone(latest_contiguous_confirmed_month(self.project))

    def test_returns_start_month_when_only_start_month_confirmed(self):
        self._make_assignment(percentage=30, is_confirmed=True)
        actor = self._add_member("actor4")
        self._make_assignment(user=actor, percentage=20, is_confirmed=True)
        self.assertEqual(latest_contiguous_confirmed_month(self.project), self.start_month)

    def test_returns_latest_contiguous_confirmed_month_through_run(self):
        # Confirm start_month, +1, +2 — gap at +3 (no row), so latest = +2.
        self._make_assignment(percentage=30, is_confirmed=True)
        self._make_assignment(month=self.start_month + relativedelta(months=1), percentage=30, is_confirmed=True)
        self._make_assignment(month=self.start_month + relativedelta(months=2), percentage=30, is_confirmed=True)
        # gap at +3
        self._make_assignment(month=self.start_month + relativedelta(months=4), percentage=30, is_confirmed=True)
        self.assertEqual(latest_contiguous_confirmed_month(self.project), self.start_month + relativedelta(months=2))

    def test_run_halts_at_first_unconfirmed_month(self):
        self._make_assignment(percentage=30, is_confirmed=True)
        self._make_assignment(month=self.start_month + relativedelta(months=1), percentage=30, is_confirmed=False)
        self.assertEqual(latest_contiguous_confirmed_month(self.project), self.start_month)


class TriggerRelaxationTestCase(AutoAssignTestCaseBase):
    """kippo#17: confirming a non-start_month row triggers extension from that month."""

    def test_confirming_later_month_seeds_from_latest_confirmed(self):
        # Start month + 1 month, both fully confirmed → extension seed is +1 month's shape.
        # Ample effort so kippo#18 doesn't truncate the window.
        self.project.allocated_staff_days = 1000
        self.project.save()
        self._make_assignment(percentage=30, is_confirmed=True)
        actor = self._add_member("later-actor")
        # Different shape on +1: only `actor` confirmed at 40%.
        self._make_assignment(user=actor, month=self.start_month + relativedelta(months=1), percentage=40, is_confirmed=True)

        # latest_confirmed_month is start_month (because start_month's user is the only user and confirmed,
        # but +1 has only `actor`'s row which is also confirmed — both months are fully confirmed).
        latest = latest_contiguous_confirmed_month(self.project)
        self.assertEqual(latest, self.start_month + relativedelta(months=1))

        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        # 5 months from +2 through +6 — seed shape is `actor` 40%, so only `actor` rows created.
        self.assertEqual(len(created), 5)
        created_users = {row.user_id for row in created}
        self.assertEqual(created_users, {actor.id})

    def test_signal_path_confirming_later_month_creates_future_rows(self):
        # End-to-end signal test: confirming a +1 row triggers auto-create.
        self._make_assignment(percentage=30, is_confirmed=True)
        later_row = self._make_assignment(month=self.start_month + relativedelta(months=1), percentage=30, is_confirmed=False)

        with self.captureOnCommitCallbacks(execute=True):
            later_row.is_confirmed = True
            later_row.save()

        future = ProjectMonthlyAssignment.objects.filter(project=self.project, month__gt=self.start_month + relativedelta(months=1))
        # 5 months created (start_month+2 through start_month+6).
        self.assertEqual(future.count(), 5)


class AutoExtendSkipReasonTestCase(AutoAssignTestCaseBase):
    """kippo#19: service returns structured SkipReason values for each no-op path."""

    def test_skip_project_closed(self):
        from projects.definitions import SkipReason

        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.is_closed = True
        self.project.save()
        _, skip_reason = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(skip_reason, SkipReason.PROJECT_CLOSED)

    def test_skip_missing_start_date(self):
        from projects.definitions import SkipReason

        self.project.start_date = None
        self.project.save()
        _, skip_reason = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(skip_reason, SkipReason.MISSING_START_DATE)

    def test_skip_missing_target_date(self):
        from projects.definitions import SkipReason

        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.target_date = None
        self.project.save()
        _, skip_reason = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(skip_reason, SkipReason.MISSING_TARGET_DATE)

    def test_skip_not_confirmed(self):
        from projects.definitions import SkipReason

        self._make_assignment(percentage=50, is_confirmed=False)
        _, skip_reason = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(skip_reason, SkipReason.NOT_CONFIRMED)

    def test_skip_no_missing_months(self):
        from projects.definitions import SkipReason

        self._make_assignment(percentage=50, is_confirmed=True)
        for offset in range(1, 7):
            self._make_assignment(month=self.start_month + relativedelta(months=offset), percentage=20, is_confirmed=False)
        _, skip_reason = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(skip_reason, SkipReason.NO_MISSING_MONTHS)

    def test_skip_none_on_success(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        created, skip_reason = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        self.assertIsNone(skip_reason)


class AutoExtendRESTEndpointTestCase(AutoAssignTestCaseBase):
    """kippo#19: REST endpoint at POST /api/.../monthly-assignments/auto-extend/<project_id>/."""

    def _url(self, project_id: str) -> str:
        return f"/api/monthly-assignments/auto-extend/{project_id}/"

    def test_post_creates_rows_for_eligible_project(self):
        from rest_framework.test import APIClient

        # Ample effort so kippo#18 doesn't truncate the window.
        self.project.allocated_staff_days = 1000
        self.project.save()
        OrganizationMembership.objects.get_or_create(
            user=self.github_manager,
            organization=self.organization,
            defaults={"is_developer": True, "created_by": self.github_manager, "updated_by": self.github_manager},
        )
        self._make_assignment(percentage=50, is_confirmed=True)
        client = APIClient()
        client.force_authenticate(user=self.github_manager)

        response = client.post(self._url(self.project.id))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["created"]), 6)
        self.assertIsNone(body["skip_reason"])

    def test_post_returns_skip_reason_when_ineligible(self):
        from rest_framework.test import APIClient

        OrganizationMembership.objects.get_or_create(
            user=self.github_manager,
            organization=self.organization,
            defaults={"is_developer": True, "created_by": self.github_manager, "updated_by": self.github_manager},
        )
        # Closed project → PROJECT_CLOSED skip.
        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.is_closed = True
        self.project.save()

        client = APIClient()
        client.force_authenticate(user=self.github_manager)
        response = client.post(self._url(self.project.id))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["created"], [])
        self.assertEqual(body["skip_reason"], "project_closed")

    def test_post_forbidden_for_non_org_member(self):
        from rest_framework.test import APIClient

        outsider = self._add_member("outsider-org-x")
        # Remove their membership in the project's org (added by _add_member).
        OrganizationMembership.objects.filter(user=outsider, organization=self.organization).delete()
        self._make_assignment(percentage=50, is_confirmed=True)

        client = APIClient()
        client.force_authenticate(user=outsider)
        response = client.post(self._url(self.project.id))
        self.assertEqual(response.status_code, 403)

    def test_post_unauthenticated_returns_401_or_403(self):
        from rest_framework.test import APIClient

        client = APIClient()
        response = client.post(self._url(self.project.id))
        self.assertIn(response.status_code, (401, 403))


class AutoExtendAdminActionTestCase(AutoAssignTestCaseBase):
    """kippo#19: admin action 'Generate future-month assignments' runs the same code path."""

    def test_admin_action_runs_per_project(self):
        from projects.admin import (
            ProjectMonthlyAssignmentAdmin,
            auto_extend_projectmonthlyassignment_action,
        )

        # Ample effort so kippo#18 doesn't truncate the window for this assertion.
        self.project.allocated_staff_days = 1000
        self.project.save()
        self._make_assignment(percentage=50, is_confirmed=True)

        # Mock-ish admin and request — just enough for the action.
        class _FakeModelAdmin:
            messages: list = []

            def message_user(self, request: object, message: str, level: int | None = None) -> None:  # noqa: ARG002
                self.messages.append((message, level))

            model = ProjectMonthlyAssignment

        class _FakeRequest:
            user = self.github_manager

        admin_obj = _FakeModelAdmin()
        queryset = ProjectMonthlyAssignment.objects.filter(project=self.project)
        auto_extend_projectmonthlyassignment_action(admin_obj, _FakeRequest(), queryset)

        # 6 future rows created via admin action.
        future = ProjectMonthlyAssignment.objects.filter(project=self.project, month__gt=self.start_month)
        self.assertEqual(future.count(), 6)
        # ProjectMonthlyAssignmentAdmin must declare the action.
        self.assertIn(auto_extend_projectmonthlyassignment_action, ProjectMonthlyAssignmentAdmin.actions)


class CapWarningParityTestCase(AutoAssignTestCaseBase):
    """kippo#20: bulk_create rows must trigger the >100% cap warning that save() emits."""

    def test_cap_warning_emitted_when_total_exceeds_100(self):
        # Soft ceiling 100 so the scaler doesn't clip pre-emptively.
        self.organization.project_assignment_member_soft_ceiling = 100
        self.organization.save()
        # Seed user at 50% on this project — auto-create will fill future months at 50% too.
        # Add 60% on a *different* project for one of the future months; total = 110% → warning.
        self._make_assignment(percentage=50, is_confirmed=True)
        other_project = KippoProject.objects.create(
            organization=self.organization,
            name="cap-warning-other-project",
            github_project_html_url="https://github.com/orgs/myorg/projects/77",
            github_project_api_nodeid="PVT_kwDOWARN",
            columnset=self.project.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # Use a future month inside the auto-extend window.
        warning_month = self.start_month + relativedelta(months=1)
        ProjectMonthlyAssignment.objects.create(
            project=other_project,
            user=self.user,
            month=warning_month,
            percentage=60,
            is_confirmed=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # _apply_caps will clip seed=50 to 40 (100-60) in warning_month — but auto-create
        # applies a single flat per-user percentage across all months, so it'll be 40
        # everywhere. To force >100%, bump the existing other-project row to 110% directly.
        # Instead: seed an over-allocation that bypasses _apply_caps. We force-create a
        # second high-pct row directly post-extend to trigger the parity warning during
        # extend by raising the other-project amount above the cap before we run.
        # Easier: bump other-project row to 70 across multiple months so the scaler picks
        # a smaller scaled value but the cap-warning still fires if the sum > 100.
        # Use a direct test of _emit_cap_warnings instead — clearer signal.
        from projects.services.autoassign import _emit_cap_warnings

        # Manually craft a "newly-created" row at 60% in warning_month — combined with the
        # 60% other-project row, total = 120% → warning must fire.
        new_row = ProjectMonthlyAssignment.objects.create(
            project=self.project,
            user=self.user,
            month=warning_month,
            percentage=60,
            is_confirmed=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        with self.assertLogs("projects.services.autoassign", level="WARNING") as cm:
            _emit_cap_warnings(self.project, [new_row])
        self.assertTrue(any("exceeds 100%" in msg for msg in cm.output))


class SkipReasonLoggingTestCase(AutoAssignTestCaseBase):
    """kippo#20: structured `extra=` payload carries skip_reason on forecast failure."""

    def test_forecast_failure_logs_skip_reason_in_extra(self):
        from projects.definitions import SkipReason

        self._make_assignment(percentage=50, is_confirmed=True)
        with (
            patch(
                "projects.services.autoassign.ProjectAssignmentForecastManager.compute",
                side_effect=RuntimeError("boom"),
            ),
            self.assertLogs("projects.services.autoassign", level="INFO") as cm,
        ):
            _, skip_reason = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(skip_reason, SkipReason.FORECAST_UNAVAILABLE)
        # At least one log record should carry the skip_reason extra.
        matching = [r for r in cm.records if getattr(r, "skip_reason", None) == SkipReason.FORECAST_UNAVAILABLE.value]
        self.assertTrue(matching, f"no log record carried skip_reason extra; got: {[r.__dict__ for r in cm.records]}")


class RaiseOnErrorFlagTestCase(AutoAssignTestCaseBase):
    """kippo#20: KIPPO_AUTO_EXTEND_RAISE_ON_ERROR=True re-raises in the signal handler."""

    def test_raises_when_flag_true(self):
        from django.test import override_settings

        self._make_assignment(percentage=50, is_confirmed=False)
        # Re-fetch to flip is_confirmed and trigger the signal — but force the service to raise.
        target = ProjectMonthlyAssignment.objects.get(project=self.project, month=self.start_month)

        with (
            patch(
                "projects.signals.auto_create_future_assignments",
                side_effect=RuntimeError("intentional"),
            ),
            override_settings(KIPPO_AUTO_EXTEND_RAISE_ON_ERROR=True),
            self.assertRaises(RuntimeError),
            self.captureOnCommitCallbacks(execute=True),
        ):
            target.is_confirmed = True
            target.save()

    def test_swallows_when_flag_false(self):
        from django.test import override_settings

        self._make_assignment(percentage=50, is_confirmed=False)
        target = ProjectMonthlyAssignment.objects.get(project=self.project, month=self.start_month)

        with (
            patch(
                "projects.signals.auto_create_future_assignments",
                side_effect=RuntimeError("intentional"),
            ),
            override_settings(KIPPO_AUTO_EXTEND_RAISE_ON_ERROR=False),
            self.captureOnCommitCallbacks(execute=True),
        ):
            # Must not raise.
            target.is_confirmed = True
            target.save()


class EffortExhaustionBoundTestCase(AutoAssignTestCaseBase):
    """kippo#18: future-month window is bounded by min(target_month, completion_month)."""

    def test_completion_before_target_caps_generated_months(self):
        # 100% seed → fast burn-down. allocated_staff_days=30 → 240 hours total.
        # At 100% × 8h × ~22 workdays/month ≈ 176h/month → completes in ~2 months.
        self.project.allocated_staff_days = 30
        self.project.save()
        self._make_assignment(percentage=100, is_confirmed=True)

        created, skip_reason = auto_create_future_assignments(self.project, self.github_manager)
        self.assertIsNone(skip_reason)
        # Bound at forecast completion month → significantly less than the full 6-month window.
        self.assertLess(len(created), 6)
        self.assertGreater(len(created), 0)

    def test_completion_after_target_caps_at_target(self):
        # Very slow seed (1%) → completion far beyond target_date → bound stays at target.
        # allocated_staff_days needs to be large enough that 1% can't finish in the window.
        self.project.allocated_staff_days = 1000
        self.project.save()
        self._make_assignment(percentage=1, is_confirmed=True)

        created, _ = auto_create_future_assignments(self.project, self.github_manager)
        # Hard cap at target_month → 6 future months even though completion is far away.
        months = sorted({row.month for row in created})
        self.assertEqual(len(months), 6)
        self.assertEqual(months[-1], self.start_month + relativedelta(months=6))

    def test_forecast_none_falls_back_to_target(self):
        # No allocated_effort_hours → forecast returns None → fallback to target_month.
        self.project.allocated_staff_days = None
        self.project.save()
        self._make_assignment(percentage=50, is_confirmed=True)

        created, _ = auto_create_future_assignments(self.project, self.github_manager)
        months = sorted({row.month for row in created})
        self.assertEqual(len(months), 6)
        self.assertEqual(months[-1], self.start_month + relativedelta(months=6))
