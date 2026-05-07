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

    def test_save_hard_fails_on_confirmed_total_over_100(self):
        existing = self._make_assignment(percentage=70, is_confirmed=True)
        existing.save()

        # Same user/org/month, would push confirmed total to 130%
        # NOTE: needs a second project in the same org to test cross-project totals
        second_project = KippoProject.objects.create(
            name="second-project",
            organization=self.organization,
            columnset=self.project.columnset,
            start_date=datetime.date(2026, 3, 15),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        offending = self._make_assignment(project=second_project, percentage=60, is_confirmed=True)
        with self.assertRaises(ValidationError) as ctx:
            offending.save()
        self.assertEqual(ctx.exception.error_dict["__all__"][0].code, "confirmed_assignment_exceeds_cap")

    def test_save_warns_only_on_unconfirmed_total_over_100(self):
        # Two unconfirmed rows summing to 130% — should warn but not raise
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
        self.assertTrue(any("exceeds 100%%" in record.getMessage() or "exceeds 100%" in record.getMessage() for record in logs.records))
        self.assertIsNotNone(second.pk)

    def test_save_does_not_double_count_self_when_updating_confirmed_row(self):
        assignment = self._make_assignment(percentage=80, is_confirmed=True)
        assignment.save()
        # Update same row's percentage upward — must not see itself in the existing total
        assignment.percentage = 95
        assignment.save()  # must not raise
        self.assertEqual(ProjectMonthlyAssignment.objects.get(pk=assignment.pk).percentage, 95)

    def test_save_unconfirmed_does_not_count_against_confirmed_cap(self):
        # 90% unconfirmed already exists; adding 50% confirmed should succeed because cap
        # only sums *confirmed* rows.
        unconfirmed = self._make_assignment(percentage=90, is_confirmed=False)
        unconfirmed.save()

        second_project = KippoProject.objects.create(
            name="confirmed-on-top",
            organization=self.organization,
            columnset=self.project.columnset,
            start_date=datetime.date(2026, 3, 15),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        confirmed = self._make_assignment(project=second_project, percentage=50, is_confirmed=True)
        confirmed.save()  # must not raise — confirmed total is only 50
        self.assertIsNotNone(confirmed.pk)

    def test_save_promote_unconfirmed_to_confirmed_rejects_when_over_cap(self):
        # 70% confirmed elsewhere, 50% unconfirmed on this row → toggling to confirmed
        # would push confirmed total to 120% → must reject.
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
        with self.assertRaises(ValidationError) as ctx:
            candidate.save()
        self.assertEqual(ctx.exception.error_dict["__all__"][0].code, "confirmed_assignment_exceeds_cap")


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
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = {row["id"] for row in response.json()["results"]}
        self.assertIn(self.assignment_apr.id, ids)
        self.assertIn(self.assignment_may.id, ids)
        self.assertNotIn(self.other_assignment.id, ids)

    def test_list_returns_all_orgs_for_superuser(self):
        superuser = KippoUser.objects.create(username="forecast-super", is_superuser=True, is_staff=True)
        admin_client = APIClient()
        admin_client.force_authenticate(user=superuser)
        response = admin_client.get(self.list_url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = {row["id"] for row in response.json()["results"]}
        self.assertIn(self.assignment_apr.id, ids)
        self.assertIn(self.other_assignment.id, ids)

    def test_filter_by_project(self):
        response = self.client.get(self.list_url, {"project": str(self.project.id)})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = {row["id"] for row in response.json()["results"]}
        self.assertEqual(ids, {self.assignment_apr.id, self.assignment_may.id})

    def test_filter_by_user(self):
        response = self.client.get(self.list_url, {"user": str(self.user.id)})
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
