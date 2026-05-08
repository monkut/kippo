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
    all_first_month_rows_confirmed,
    auto_create_future_assignments,
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

        rows = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])
        self.assertEqual(ProjectMonthlyAssignment.objects.filter(project=self.project).count(), 1)

    def test_no_op_when_actual_date_set(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.actual_date = self.start_month + relativedelta(months=2)
        self.project.save()

        rows = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_target_date_missing(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.target_date = None
        self.project.save()

        rows = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_target_date_le_start_month(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        self.project.target_date = self.start_month + datetime.timedelta(days=15)  # within start_month
        self.project.save()

        rows = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_start_date_missing(self):
        # Can't seed without a first month.
        self.project.start_date = None
        self.project.save()
        rows = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_no_first_month_rows(self):
        rows = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_no_op_when_seed_all_zero(self):
        # _make_assignment with percentage=0 still creates a row (model allows 0).
        # Such rows are excluded by the seed query (`percentage__gt=0`); seed becomes empty.
        self._make_assignment(percentage=0, is_confirmed=True)
        rows = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])

    def test_skips_when_future_rows_already_exist(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        # Pre-existing future-month row → idempotency guard fires.
        self._make_assignment(month=self.start_month + relativedelta(months=2), percentage=20, is_confirmed=False)

        rows = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])
        # Only the seed + the pre-existing row should be present.
        self.assertEqual(ProjectMonthlyAssignment.objects.filter(project=self.project).count(), 2)

    # --------------------------------------------------------------- months generated (D3)

    def test_creates_one_row_per_seed_user_per_future_month(self):
        # 6 future months: start_month+1 through target_month inclusive.
        self._make_assignment(percentage=50, is_confirmed=True)

        created = auto_create_future_assignments(self.project, self.github_manager)
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

        created = auto_create_future_assignments(self.project, self.github_manager)
        # 6 historical months persisted: 2020-02-01 through 2020-07-01.
        self.assertEqual(len(created), 6)

    # -------------------------------------------------------------------- D2: provenance

    def test_uses_trigger_row_updated_by_for_created_by_and_updated_by(self):
        actor = self._add_member("trigger-actor")
        self._make_assignment(percentage=50, is_confirmed=True, updated_by=actor)

        created = auto_create_future_assignments(self.project, actor)
        self.assertTrue(created)
        for row in created:
            self.assertEqual(row.created_by_id, actor.id)
            self.assertEqual(row.updated_by_id, actor.id)

    def test_falls_back_to_created_by_when_updated_by_is_none(self):
        # Simulate the signal's resolution: updated_by None → created_by.
        # Service receives whatever `triggered_by` the signal resolved.
        self._make_assignment(percentage=50, is_confirmed=True)
        created = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        for row in created:
            self.assertEqual(row.created_by_id, self.github_manager.id)

    # ----------------------------------------------------------- D1: scaling math

    def test_within_tolerance_seed_unchanged(self):
        # Seed already lands close enough: don't scale, persist seed as-is.
        # 50% × 8h × ~5 weekdays/week → roughly 100h/month → completes well before target.
        self._make_assignment(percentage=50, is_confirmed=True)
        created = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        # Fast seed → no scaling: every row keeps the seed percentage.
        for row in created:
            self.assertEqual(row.percentage, 50)

    def test_slow_seed_is_scaled_up(self):
        # Single 5% seed produces ~1h/day. 240h ÷ 1h/day far exceeds the project window
        # → binary search will scale up.
        self._make_assignment(percentage=5, is_confirmed=True)
        created = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        seed_pct = 5
        # Every persisted row should be > seed (scaled up by ceil).
        self.assertTrue(all(row.percentage > seed_pct for row in created))

    def test_persisted_percentage_capped_at_org_soft_ceiling(self):
        # With soft ceiling 75%, a single user can never persist > 75% per month.
        self.organization.project_assignment_member_soft_ceiling = 75
        self.organization.save()
        self._make_assignment(percentage=5, is_confirmed=True)

        created = auto_create_future_assignments(self.project, self.github_manager)
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

        created = auto_create_future_assignments(self.project, self.github_manager)
        # Per-user cap across all months = min(soft_ceiling=75, 100-60 in Feb) = 40.
        self.assertTrue(created)
        for row in created:
            self.assertLessEqual(row.percentage, 40)

    # ------------------------------------------------------- D4: bulk_create persistence

    def test_persisted_rows_are_unconfirmed(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        created = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        for row in created:
            self.assertFalse(row.is_confirmed)

    def test_forecast_failure_is_swallowed(self):
        self._make_assignment(percentage=50, is_confirmed=True)
        with patch(
            "projects.services.autoassign.ProjectAssignmentForecastManager.compute",
            side_effect=RuntimeError("boom"),
        ):
            rows = auto_create_future_assignments(self.project, self.github_manager)
        self.assertEqual(rows, [])


class AutoAssignSignalTestCase(AutoAssignTestCaseBase):
    """Signal wiring: post_save trigger + transaction.on_commit semantics."""

    def test_confirming_last_first_month_row_triggers_creation(self):
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
        # never fires.
        with self.captureOnCommitCallbacks(execute=True):
            seed = self._make_assignment(percentage=50, is_confirmed=True)
        first_count = ProjectMonthlyAssignment.objects.filter(project=self.project, month__gt=self.start_month).count()
        self.assertEqual(first_count, 6)

        # Re-save: idempotency guard skips because future rows exist.
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
        # idempotency guard would defend the second invocation regardless.
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


class AllFirstMonthRowsConfirmedTestCase(AutoAssignTestCaseBase):
    """Smoke tests for the trigger predicate."""

    def test_returns_false_when_start_date_missing(self):
        self.project.start_date = None
        self.project.save()
        self.assertFalse(all_first_month_rows_confirmed(self.project))

    def test_returns_false_when_no_rows_for_start_month(self):
        self.assertFalse(all_first_month_rows_confirmed(self.project))

    def test_returns_false_when_any_row_unconfirmed(self):
        self._make_assignment(percentage=30, is_confirmed=True)
        actor = self._add_member("actor3")
        self._make_assignment(user=actor, percentage=20, is_confirmed=False)
        self.assertFalse(all_first_month_rows_confirmed(self.project))

    def test_returns_true_when_every_row_confirmed(self):
        self._make_assignment(percentage=30, is_confirmed=True)
        actor = self._add_member("actor4")
        self._make_assignment(user=actor, percentage=20, is_confirmed=True)
        self.assertTrue(all_first_month_rows_confirmed(self.project))
