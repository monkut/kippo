"""Tests for GET /api/projects/<id>/members/ — kippo#233.

Lists active org members for the project's organization. Source for kippo-ui's
add-assignment user picker (kippo-ui#57).
"""

from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient


class ProjectMembersEndpointTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.project = created["KippoProject"]
        self.user = created["KippoUser"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Cross-org user — should not appear in this project's members
        self.other_org = KippoOrganization.objects.create(
            name="other-org-for-members-test",
            github_organization_name="otherorg-members",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.outsider = KippoUser.objects.create(username="members-outsider", email="outsider@example.com")
        OrganizationMembership.objects.create(
            user=self.outsider,
            organization=self.other_org,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/members/"

    def _add_org_member(self, username: str, *, is_developer: bool = True, is_project_manager: bool = False) -> KippoUser:
        user = KippoUser.objects.create(username=username, email=f"{username}@example.com")
        OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            is_developer=is_developer,
            is_project_manager=is_project_manager,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        return user

    def test_unauthenticated_returns_401(self):
        anon = APIClient()
        response = anon.get(self.url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_returns_active_members_of_project_organization(self):
        self._add_org_member("dev-1")
        self._add_org_member("dev-2")

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        usernames = {row["username"] for row in response.json()}
        # The default setup user (octocat) plus the two dev users
        self.assertIn("octocat", usernames)
        self.assertIn("dev-1", usernames)
        self.assertIn("dev-2", usernames)
        # outsider is not in this org
        self.assertNotIn("members-outsider", usernames)

    def test_excludes_inactive_users(self):
        active = self._add_org_member("active-dev")
        inactive = self._add_org_member("inactive-dev")
        inactive.is_active = False
        inactive.save()

        response = self.client.get(self.url)
        usernames = {row["username"] for row in response.json()}
        self.assertIn(active.username, usernames)
        self.assertNotIn(inactive.username, usernames)

    def test_response_shape(self):
        self._add_org_member("dev-shape", is_developer=True, is_project_manager=False)
        response = self.client.get(self.url)
        rows = response.json()
        self.assertGreater(len(rows), 0)
        sample = rows[0]
        for field in ["user_id", "username", "display_name", "github_login", "is_developer", "is_project_manager"]:
            self.assertIn(field, sample)

    def test_includes_role_flags_from_organization_membership(self):
        pm_user = self._add_org_member("the-pm", is_developer=False, is_project_manager=True)
        response = self.client.get(self.url)
        target = next(row for row in response.json() if row["username"] == pm_user.username)
        self.assertTrue(target["is_project_manager"])
        self.assertFalse(target["is_developer"])

    def test_cross_org_user_gets_404(self):
        cross = APIClient()
        cross.force_authenticate(user=self.outsider)
        response = cross.get(self.url)
        # Org-scoped queryset hides the project from this user — DRF returns 404
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_openapi_schema_exposes_members_endpoint(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        path = f"{settings.URL_PREFIX}/api/projects/{{id}}/members/"
        self.assertIn(path, schema["paths"])
        self.assertIn("get", schema["paths"][path])
