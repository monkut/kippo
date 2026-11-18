"""Tests for POST /api/projects/{id}/participants/ — kiconiaworks/kippo#72.

Adds a project participant: one unconfirmed ProjectMonthlyAssignment per month over
the project period (start_date month … target_date month, inclusive).
"""

import datetime
from http import HTTPStatus
from unittest.mock import patch

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.db.models import QuerySet
from django.test import TestCase
from rest_framework.test import APIClient

from projects.definitions import ProjectRoles
from projects.models import ProjectMonthlyAssignment

PERCENTAGE = 40


class ProjectParticipantsEndpointTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.project.start_date = datetime.date(2026, 3, 15)
        self.project.target_date = datetime.date(2026, 6, 10)
        self.project.save()
        self.github_manager = KippoUser.objects.get(username="github-manager")

        self.member = KippoUser.objects.create(username="participant", email="participant@example.com")
        OrganizationMembership.objects.create(
            user=self.member,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.other_org = KippoOrganization.objects.create(
            name="other-org",
            github_organization_name="otherorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.outsider = KippoUser.objects.create(username="outsider", email="outsider@example.com")
        OrganizationMembership.objects.create(
            user=self.outsider,
            organization=self.other_org,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _url(self, project_id: object) -> str:
        return f"{settings.URL_PREFIX}/api/projects/{project_id}/participants/"

    def _rows(self) -> QuerySet:
        return ProjectMonthlyAssignment.objects.filter(project=self.project, user=self.member).order_by("month")

    def test_creates_one_unconfirmed_row_per_month(self):
        payload = {"user": self.member.id, "percentage": PERCENTAGE, "role": ProjectRoles.TESTER.value}
        response = self.client.post(self._url(self.project.id), payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)

        expected_months = [datetime.date(2026, m, 1) for m in (3, 4, 5, 6)]
        rows = list(self._rows())
        self.assertEqual([r.month for r in rows], expected_months)
        self.assertTrue(all(r.percentage == PERCENTAGE and r.role == ProjectRoles.TESTER.value and not r.is_confirmed for r in rows))
        self.assertTrue(all(r.created_by == self.user and r.updated_by == self.user for r in rows))

        body = response.json()
        self.assertEqual([r["month"] for r in body["created"]], [m.isoformat() for m in expected_months])
        self.assertEqual(body["skipped_months"], [])

    def test_role_defaults_to_empty(self):
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        self.assertTrue(all(r.role == "" for r in self._rows()))

    def test_skips_existing_months_without_overwriting(self):
        existing = ProjectMonthlyAssignment.objects.create(
            project=self.project,
            user=self.member,
            month=datetime.date(2026, 4, 1),
            percentage=80,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)

        body = response.json()
        self.assertEqual(body["skipped_months"], ["2026-04-01"])
        self.assertEqual([r["month"] for r in body["created"]], ["2026-03-01", "2026-05-01", "2026-06-01"])
        existing.refresh_from_db()
        self.assertEqual(existing.percentage, 80)
        self.assertEqual(self._rows().count(), 4)

    def test_all_months_existing_returns_200(self):
        self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": 10}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK, response.content)
        body = response.json()
        self.assertEqual(body["created"], [])
        self.assertEqual(body["skipped_months"], ["2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"])
        self.assertTrue(all(r.percentage == PERCENTAGE for r in self._rows()))

    def test_missing_start_date_returns_400(self):
        self.project.start_date = None
        self.project.save()
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)
        self.assertIn("start_date", response.json())
        self.assertFalse(self._rows().exists())

    def test_missing_target_date_returns_400(self):
        self.project.target_date = None
        self.project.save()
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)
        self.assertIn("target_date", response.json())
        self.assertFalse(self._rows().exists())

    def test_missing_percentage_returns_400(self):
        response = self.client.post(self._url(self.project.id), {"user": self.member.id}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)
        self.assertIn("percentage", response.json())
        self.assertFalse(self._rows().exists())

    def test_invalid_role_returns_400(self):
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE, "role": "boss"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)
        self.assertIn("role", response.json())

    def test_user_not_in_project_organization_returns_400(self):
        response = self.client.post(self._url(self.project.id), {"user": self.outsider.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)
        self.assertFalse(ProjectMonthlyAssignment.objects.filter(project=self.project, user=self.outsider).exists())

    def test_non_member_caller_forbidden(self):
        client = APIClient()
        client.force_authenticate(user=self.outsider)
        response = client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)
        self.assertFalse(self._rows().exists())

    def test_unauthenticated_returns_401(self):
        response = APIClient().post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_unknown_project_returns_404(self):
        unknown_project_id = "00000000-0000-4000-8000-000000000000"
        response = self.client.post(self._url(unknown_project_id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_does_not_trigger_auto_extend(self):
        with (
            # Patch the service, not the post_save signal: kippo#70 removes the signal module.
            patch("projects.services.autoassign.auto_create_future_assignments") as auto_extend,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        auto_extend.assert_not_called()
        self.assertEqual(callbacks, [])
        self.assertEqual(ProjectMonthlyAssignment.objects.filter(project=self.project).count(), 4)
