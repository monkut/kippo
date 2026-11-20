"""Tests for /api/projects/{id}/participants/ — kiconiaworks/kippo#72, #75.

POST adds a project participant: the ProjectParticipant plus one ProjectMonthlyAssignment per
remaining month (the later of the current month and the start_date month … target_date month).
GET lists the project's participants. "Today" is pinned to TODAY (current month = 2026-04).
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
from projects.models import ProjectMonthlyAssignment, ProjectParticipant
from projects.tests.helpers import make_participant

PERCENTAGE = 40
TODAY = datetime.date(2026, 4, 10)


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

        today_patcher = patch("django.utils.timezone.localdate", return_value=TODAY)
        today_patcher.start()
        self.addCleanup(today_patcher.stop)

    def _url(self, project_id: object) -> str:
        return f"{settings.URL_PREFIX}/api/projects/{project_id}/participants/"

    def _rows(self) -> QuerySet:
        return ProjectMonthlyAssignment.objects.filter(project=self.project, user=self.member).order_by("month")

    def test_creates_participant_and_one_row_per_remaining_month(self):
        payload = {"user": self.member.id, "percentage": PERCENTAGE, "role": ProjectRoles.TESTER.value}
        response = self.client.post(self._url(self.project.id), payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)

        participant = ProjectParticipant.objects.get(project=self.project, user=self.member)
        self.assertEqual((participant.percentage, participant.role), (PERCENTAGE, ProjectRoles.TESTER.value))
        # R1: March is past (current month = April) → April … June only.
        expected_months = [datetime.date(2026, m, 1) for m in (4, 5, 6)]
        rows = list(self._rows())
        self.assertEqual([r.month for r in rows], expected_months)
        self.assertTrue(all(r.percentage == PERCENTAGE and r.role == ProjectRoles.TESTER.value and not r.is_confirmed for r in rows))
        self.assertTrue(all(r.created_by == self.user and r.updated_by == self.user for r in rows))
        self.assertTrue(all(r.participant_id == participant.id for r in rows))

        body = response.json()
        self.assertEqual([r["month"] for r in body["created"]], [m.isoformat() for m in expected_months])
        self.assertEqual(body["skipped_months"], [])

    def test_role_defaults_to_empty(self):
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        self.assertTrue(all(r.role == "" for r in self._rows()))

    def test_skips_existing_months_without_overwriting(self):
        make_participant(self.project, self.member, percentage=80)
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
        self.assertEqual([r["month"] for r in body["created"]], ["2026-05-01", "2026-06-01"])
        existing.refresh_from_db()
        self.assertEqual(existing.percentage, 80)
        self.assertEqual(self._rows().count(), 3)
        # An existing participant keeps its stored defaults.
        self.assertEqual(ProjectParticipant.objects.get(project=self.project, user=self.member).percentage, 80)

    def test_all_months_existing_returns_200(self):
        self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": 10}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK, response.content)
        body = response.json()
        self.assertEqual(body["created"], [])
        self.assertEqual(body["skipped_months"], ["2026-04-01", "2026-05-01", "2026-06-01"])
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
        self.assertIn("user", response.json())
        self.assertFalse(ProjectMonthlyAssignment.objects.filter(project=self.project, user=self.outsider).exists())
        self.assertFalse(ProjectParticipant.objects.filter(project=self.project, user=self.outsider).exists())

    def test_future_start_month_starts_at_start_date(self):
        self.project.start_date = datetime.date(2026, 5, 20)
        self.project.save()
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        self.assertEqual([r.month for r in self._rows()], [datetime.date(2026, 5, 1), datetime.date(2026, 6, 1)])

    def test_ended_project_returns_400(self):
        self.project.target_date = datetime.date(2026, 4, 9)  # the day before TODAY
        self.project.save()
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)
        self.assertIn("完了予定日: 2026-04-09", response.json()["target_date"][0])
        self.assertFalse(ProjectParticipant.objects.filter(project=self.project, user=self.member).exists())
        self.assertFalse(self._rows().exists())

    def test_target_date_today_is_allowed(self):
        self.project.target_date = TODAY
        self.project.save()
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        self.assertEqual([r.month for r in self._rows()], [datetime.date(2026, 4, 1)])

    def test_ended_project_rejects_existing_participant_too(self):
        make_participant(self.project, self.member, percentage=PERCENTAGE)
        self.project.target_date = datetime.date(2026, 4, 9)
        self.project.save()
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)
        self.assertIn("target_date", response.json())

    def test_start_more_than_6_months_ahead_returns_400(self):
        self.project.start_date = datetime.date(2026, 10, 11)  # TODAY + 6 months + 1 day
        self.project.target_date = datetime.date(2026, 12, 31)
        self.project.save()
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)
        message = response.json()["start_date"][0]
        self.assertIn("開始日（2026-10-11）が6か月以上先", message)
        self.assertIn("2026-04-11 以降に追加", message)
        self.assertFalse(ProjectParticipant.objects.filter(project=self.project, user=self.member).exists())

    def test_start_exactly_6_months_ahead_is_allowed(self):
        self.project.start_date = datetime.date(2026, 10, 10)
        self.project.target_date = datetime.date(2026, 11, 30)
        self.project.save()
        response = self.client.post(self._url(self.project.id), {"user": self.member.id, "percentage": PERCENTAGE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        self.assertEqual([r.month for r in self._rows()], [datetime.date(2026, 10, 1), datetime.date(2026, 11, 1)])

    def test_get_lists_participants_by_username(self):
        make_participant(self.project, self.member, percentage=30, role=ProjectRoles.DEVELOPER.value)
        make_participant(self.project, self.user, percentage=60)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, HTTPStatus.OK, response.content)
        body = response.json()
        self.assertEqual([p["user_username"] for p in body], sorted([self.member.username, self.user.username]))
        member_row = next(p for p in body if p["user"] == str(self.member.id))
        self.assertEqual((member_row["percentage"], member_row["role"]), (30, ProjectRoles.DEVELOPER.value))

    def test_get_forbidden_for_non_member_caller(self):
        client = APIClient()
        client.force_authenticate(user=self.outsider)
        self.assertEqual(client.get(self._url(self.project.id)).status_code, HTTPStatus.FORBIDDEN)

    def test_monthly_assignment_post_for_non_participant_returns_400(self):
        payload = {"project": str(self.project.id), "user": self.member.id, "month": "2026-05-01", "percentage": 10}
        response = self.client.post(f"{settings.URL_PREFIX}/api/monthly-assignments/", payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)
        self.assertIn("user", response.json())

        make_participant(self.project, self.member)
        response = self.client.post(f"{settings.URL_PREFIX}/api/monthly-assignments/", payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)

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
        self.assertEqual(ProjectMonthlyAssignment.objects.filter(project=self.project).count(), 3)
