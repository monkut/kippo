"""Tests for the ProjectMonthlyAssignment model, serializer, and viewset.

Covers the existing surface as part of monkut/kippo#225 (Phase 0 of feature #224):
- Model: clean() org-membership check, month default, hard-fail on confirmed >100%.
- Model: save() warning-only on unconfirmed >100%.
- Serializer: user display + slack metadata derived from OrganizationMembership.
- ViewSet: org-scoping, filters, permission checks.
"""

import datetime
import logging
from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from projects.models import KippoProject, ProjectMonthlyAssignment
from projects.serializers import ProjectMonthlyAssignmentSerializer


class ProjectMonthlyAssignmentModelTestCase(TestCase):
    """Model validation + persistence behavior."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        # Project needs a start_date for the month-default behavior
        self.project.start_date = datetime.date(2026, 3, 15)
        self.project.save()

    def _make_assignment(self, **overrides) -> ProjectMonthlyAssignment:
        defaults = {
            "project": self.project,
            "user": self.user,
            "month": datetime.date(2026, 4, 1),
            "percentage": 50,
            "is_confirmed": False,
            "created_by": self.github_manager,
            "updated_by": self.github_manager,
        }
        defaults.update(overrides)
        return ProjectMonthlyAssignment(**defaults)

    def test_clean_rejects_user_not_in_project_organization(self):
        outsider_org = KippoOrganization.objects.create(
            name="outsider-org",
            github_organization_name="outsider",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        outsider = KippoUser.objects.create(username="outsider", email="outsider@example.com")
        OrganizationMembership.objects.create(
            user=outsider,
            organization=outsider_org,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        assignment = self._make_assignment(user=outsider)
        with self.assertRaises(ValidationError) as ctx:
            assignment.full_clean()
        self.assertEqual(ctx.exception.error_dict["__all__"][0].code, "invalid_user_organization")

    def test_clean_defaults_month_to_project_start_date_first_of_month(self):
        # start_date = 2026-03-15, default month should be 2026-03-01
        assignment = self._make_assignment(month=None)
        assignment.full_clean()
        self.assertEqual(assignment.month, datetime.date(2026, 3, 1))

    def test_save_persists_with_valid_data(self):
        assignment = self._make_assignment(percentage=40)
        assignment.save()
        self.assertIsNotNone(assignment.pk)

    def test_save_warns_only_on_confirmed_total_over_100(self):
        # Two confirmed rows summing to 130% — over-allocation is allowed; logs a warning.
        existing = self._make_assignment(percentage=70, is_confirmed=True)
        existing.save()
        second_project = KippoProject.objects.create(
            name="confirmed-warn-project",
            organization=self.organization,
            columnset=self.project.columnset,
            start_date=datetime.date(2026, 3, 15),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        with self.assertLogs("projects.models", level=logging.WARNING) as logs:
            offending = self._make_assignment(project=second_project, percentage=60, is_confirmed=True)
            offending.save()  # must not raise
        self.assertTrue(any("exceeds 100" in record.getMessage() for record in logs.records))
        self.assertIsNotNone(offending.pk)

    def test_save_warns_only_on_unconfirmed_total_over_100(self):
        # Two unconfirmed rows summing to 130% — over-allocation is allowed; logs a warning.
        existing = self._make_assignment(percentage=70, is_confirmed=False)
        existing.save()
        second_project = KippoProject.objects.create(
            name="warn-project",
            organization=self.organization,
            columnset=self.project.columnset,
            start_date=datetime.date(2026, 3, 15),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        with self.assertLogs("projects.models", level=logging.WARNING) as logs:
            second = self._make_assignment(project=second_project, percentage=60, is_confirmed=False)
            second.save()  # must not raise
        self.assertTrue(any("exceeds 100" in record.getMessage() for record in logs.records))
        self.assertIsNotNone(second.pk)

    def test_save_promote_unconfirmed_to_confirmed_allows_over_cap(self):
        # 70% confirmed elsewhere + 50% unconfirmed locally → toggling to confirmed pushes
        # the confirmed total to 120%. Allowed; warning logged.
        elsewhere = self._make_assignment(percentage=70, is_confirmed=True)
        elsewhere.save()

        second_project = KippoProject.objects.create(
            name="promote-test",
            organization=self.organization,
            columnset=self.project.columnset,
            start_date=datetime.date(2026, 3, 15),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        candidate = self._make_assignment(project=second_project, percentage=50, is_confirmed=False)
        candidate.save()
        candidate.is_confirmed = True
        with self.assertLogs("projects.models", level=logging.WARNING) as logs:
            candidate.save()  # must not raise
        self.assertTrue(any("exceeds 100" in record.getMessage() for record in logs.records))
        candidate.refresh_from_db()
        self.assertTrue(candidate.is_confirmed)


class ProjectMonthlyAssignmentSerializerTestCase(TestCase):
    """Serializer derives display/slack fields from OrganizationMembership."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.project.start_date = datetime.date(2026, 3, 15)
        self.project.save()
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Annotate user's display_name + the membership's slack fields
        self.user.first_name = "Octo"
        self.user.last_name = "Cat"
        self.user.save()
        membership = OrganizationMembership.objects.get(user=self.user, organization=self.organization)
        membership.slack_username = "octo-slack"
        membership.slack_image_url = "https://example.com/octo.png"
        membership.save()

        self.assignment = ProjectMonthlyAssignment.objects.create(
            project=self.project,
            user=self.user,
            month=datetime.date(2026, 4, 1),
            percentage=50,
            is_confirmed=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_serializer_exposes_user_username(self):
        data = ProjectMonthlyAssignmentSerializer(self.assignment).data
        self.assertEqual(data["user_username"], self.user.username)

    def test_serializer_user_display_name_from_user(self):
        data = ProjectMonthlyAssignmentSerializer(self.assignment).data
        # KippoUser.display_name composes name + parenthetical username; assert components present
        self.assertIn("Octo Cat", data["user_display_name"])

    def test_serializer_user_slack_fields_from_organization_membership(self):
        data = ProjectMonthlyAssignmentSerializer(self.assignment).data
        self.assertEqual(data["user_slack_username"], "octo-slack")
        self.assertEqual(data["user_slack_image_url"], "https://example.com/octo.png")

    def test_serializer_user_slack_fields_null_when_membership_absent(self):
        # Wipe membership to force the membership-not-found branch
        OrganizationMembership.objects.filter(user=self.user, organization=self.organization).delete()
        data = ProjectMonthlyAssignmentSerializer(self.assignment).data
        self.assertIsNone(data["user_slack_username"])
        self.assertIsNone(data["user_slack_image_url"])


class ProjectMonthlyAssignmentViewSetTestCase(TestCase):
    """ViewSet org-scoping, filters, and permissions."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.project.start_date = datetime.date(2026, 3, 15)
        self.project.save()
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Second org with its own project, user, and assignment — should be invisible to self.user
        self.other_org = KippoOrganization.objects.create(
            name="other-org",
            github_organization_name="otherorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_user = KippoUser.objects.create(username="otheruser", email="other@example.com")
        OrganizationMembership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_project = KippoProject.objects.create(
            name="other-project",
            organization=self.other_org,
            columnset=self.project.columnset,
            start_date=datetime.date(2026, 3, 15),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # Three assignments to drive filter tests
        self.assignment_apr = ProjectMonthlyAssignment.objects.create(
            project=self.project,
            user=self.user,
            month=datetime.date(2026, 4, 1),
            percentage=50,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.assignment_may = ProjectMonthlyAssignment.objects.create(
            project=self.project,
            user=self.user,
            month=datetime.date(2026, 5, 1),
            percentage=60,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_assignment = ProjectMonthlyAssignment.objects.create(
            project=self.other_project,
            user=self.other_user,
            month=datetime.date(2026, 4, 1),
            percentage=70,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.list_url = f"{settings.URL_PREFIX}/api/monthly-assignments/"
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_requires_authentication(self):
        anon = APIClient()
        response = anon.get(self.list_url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_list_org_scoped_for_regular_user(self):
        # Broad range so the current-month (JST) default doesn't scope this org-scoping check.
        response = self.client.get(self.list_url, {"month_gte": "2026-01-01"})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = {row["id"] for row in response.json()["results"]}
        self.assertIn(self.assignment_apr.id, ids)
        self.assertIn(self.assignment_may.id, ids)
        self.assertNotIn(self.other_assignment.id, ids)

    def test_list_returns_all_orgs_for_superuser(self):
        superuser = KippoUser.objects.create(username="forecast-super", is_superuser=True, is_staff=True)
        admin_client = APIClient()
        admin_client.force_authenticate(user=superuser)
        response = admin_client.get(self.list_url, {"month_gte": "2026-01-01"})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = {row["id"] for row in response.json()["results"]}
        self.assertIn(self.assignment_apr.id, ids)
        self.assertIn(self.other_assignment.id, ids)

    def test_filter_by_project(self):
        # Broad range so the fixed-date fixtures aren't scoped out by the current-month default.
        response = self.client.get(self.list_url, {"project": str(self.project.id), "month_gte": "2026-01-01"})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = {row["id"] for row in response.json()["results"]}
        self.assertEqual(ids, {self.assignment_apr.id, self.assignment_may.id})

    def test_filter_by_user(self):
        response = self.client.get(self.list_url, {"user": str(self.user.id), "month_gte": "2026-01-01"})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        results = response.json()["results"]
        self.assertGreater(len(results), 0)
        for row in results:
            self.assertEqual(row["user"], str(self.user.id))

    def test_filter_by_month_exact(self):
        response = self.client.get(self.list_url, {"month": "2026-04-01"})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        for row in response.json()["results"]:
            self.assertEqual(row["month"], "2026-04-01")

    def test_filter_by_month_gte_and_lte(self):
        response = self.client.get(
            self.list_url,
            {"month_gte": "2026-04-01", "month_lte": "2026-04-30"},
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = {row["id"] for row in response.json()["results"]}
        self.assertEqual(ids, {self.assignment_apr.id})

    def test_list_defaults_to_current_jst_month_when_no_month_param(self):
        """No month/month_gte/month_lte → only the current-month (JST) rows are returned."""
        current_month = timezone.localdate().replace(day=1)
        past_month = (current_month - datetime.timedelta(days=1)).replace(day=1)
        # Separate project so these don't collide with setUp's (project,user,month) rows.
        project = KippoProject.objects.create(
            name="current-month-default-project",
            organization=self.organization,
            columnset=self.project.columnset,
            start_date=past_month,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        current = ProjectMonthlyAssignment.objects.create(
            project=project,
            user=self.user,
            month=current_month,
            percentage=10,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        past = ProjectMonthlyAssignment.objects.create(
            project=project,
            user=self.user,
            month=past_month,
            percentage=20,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        rows = response.json()["results"]
        ids = {row["id"] for row in rows}
        self.assertIn(current.id, ids)
        self.assertNotIn(past.id, ids)
        # Every returned row is the current JST month.
        self.assertTrue(all(row["month"] == current_month.isoformat() for row in rows))

    def test_explicit_month_range_not_overridden_by_current_month_default(self):
        """A month_gte alone keeps range semantics (the current-month default must not kick in)."""
        response = self.client.get(self.list_url, {"month_gte": "2026-04-01"})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = {row["id"] for row in response.json()["results"]}
        self.assertEqual(ids, {self.assignment_apr.id, self.assignment_may.id})

    def test_create_persists_new_assignment(self):
        new_month = datetime.date(2026, 6, 1)
        payload = {
            "project": str(self.project.id),
            "user": str(self.user.id),
            "month": new_month.isoformat(),
            "percentage": 25,
            "is_confirmed": False,
        }
        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        self.assertTrue(ProjectMonthlyAssignment.objects.filter(project=self.project, user=self.user, month=new_month).exists())

    def test_partial_update_changes_percentage(self):
        url = f"{self.list_url}{self.assignment_apr.id}/"
        response = self.client.patch(url, {"percentage": 75}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assignment_apr.refresh_from_db()
        self.assertEqual(self.assignment_apr.percentage, 75)

    def test_destroy_removes_row(self):
        url = f"{self.list_url}{self.assignment_apr.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, HTTPStatus.NO_CONTENT)
        self.assertFalse(ProjectMonthlyAssignment.objects.filter(pk=self.assignment_apr.id).exists())

    def test_other_org_assignment_is_invisible_to_user(self):
        url = f"{self.list_url}{self.other_assignment.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
