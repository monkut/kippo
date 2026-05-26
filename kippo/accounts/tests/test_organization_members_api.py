"""Tests for the org-level user-listing API (kippo#14)."""

import uuid
from http import HTTPStatus

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import EmailDomain, KippoOrganization, KippoUser, OrganizationMembership


class OrganizationListTestCase(TestCase):
    """`GET /api/organizations/` — list orgs the requester belongs to."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.organization = created["KippoOrganization"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Second org the user does NOT belong to.
        self.other_organization = KippoOrganization.objects.create(
            name="other-org",
            github_organization_name="other-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()

    def test_unauthenticated_returns_401(self):
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_authenticated_user_sees_only_own_orgs(self):
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("organizations", data)
        org_ids = [o["id"] for o in data["organizations"]]
        self.assertIn(str(self.organization.id), org_ids)
        self.assertNotIn(str(self.other_organization.id), org_ids)

    def test_user_without_memberships_gets_empty_list(self):
        loner = KippoUser.objects.create(username="loner", is_staff=True)
        self.client.force_authenticate(user=loner)
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.json(), {"organizations": []})

    def test_superuser_sees_all_orgs(self):
        superuser = KippoUser.objects.create(username="root", is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=superuser)
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        org_ids = [o["id"] for o in response.json()["organizations"]]
        self.assertIn(str(self.organization.id), org_ids)
        self.assertIn(str(self.other_organization.id), org_ids)

    def test_org_response_shape(self):
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)
        org = next(o for o in response.json()["organizations"] if o["id"] == str(self.organization.id))
        self.assertEqual(set(org.keys()), {"id", "name", "github_organization_name"})
        self.assertEqual(org["name"], self.organization.name)
        self.assertEqual(org["github_organization_name"], self.organization.github_organization_name)


class OrganizationMembersAPITestCase(TestCase):
    """`GET /api/organizations/<id>/members/` — list members of one org."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.organization = created["KippoOrganization"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # PM member, with all the per-org PII populated.
        self.pm_user = KippoUser.objects.create(
            username="pm-user",
            first_name="Pat",
            last_name="Manager",
            github_login="pat-pm",
            is_staff=True,
        )
        OrganizationMembership.objects.create(
            user=self.pm_user,
            organization=self.organization,
            email="pat@github.com",
            slack_username="pat",
            slack_user_id="U02PM",
            slack_image_url="https://example.com/pat.png",
            is_developer=False,
            is_project_manager=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # Inactive developer. NOTE: OrganizationMembership.save() re-activates the user when
        # the org has an is_staff_domain — so we flip is_active=False AFTER the membership is created.
        self.inactive_user = KippoUser.objects.create(
            username="ghost",
            github_login="ghost",
            is_staff=False,
        )
        OrganizationMembership.objects.create(
            user=self.inactive_user,
            organization=self.organization,
            is_developer=True,
            is_project_manager=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.inactive_user.is_active = False
        self.inactive_user.save()

        # Second org the requester does NOT belong to.
        self.other_organization = KippoOrganization.objects.create(
            name="other-org",
            github_organization_name="other-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        EmailDomain.objects.create(
            organization=self.other_organization,
            domain="example.com",
            is_staff_domain=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_org_user = KippoUser.objects.create(username="otheruser", is_staff=True)
        OrganizationMembership.objects.create(
            user=self.other_org_user,
            organization=self.other_organization,
            is_developer=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()

    def _url(self, org_id: str | uuid.UUID) -> str:
        return f"{settings.URL_PREFIX}/api/organizations/{org_id}/members/"

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self._url(self.organization.id))
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_nonexistent_org_returns_404(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(uuid.uuid4()))
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_non_member_returns_403(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.other_organization.id))
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_member_can_list_org_members(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("members", data)
        usernames = [m["username"] for m in data["members"]]
        self.assertIn(self.user.username, usernames)
        self.assertIn(self.pm_user.username, usernames)

    def test_inactive_user_excluded_by_default(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id))
        usernames = [m["username"] for m in response.json()["members"]]
        self.assertNotIn(self.inactive_user.username, usernames)

    def test_include_inactive_opts_in(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id) + "?include_inactive=true")
        usernames = [m["username"] for m in response.json()["members"]]
        self.assertIn(self.inactive_user.username, usernames)

    def test_filter_is_developer(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id) + "?is_developer=true")
        usernames = [m["username"] for m in response.json()["members"]]
        # octocat (setup_basic_project) is is_developer=True; pm-user is is_developer=False.
        self.assertIn(self.user.username, usernames)
        self.assertNotIn(self.pm_user.username, usernames)

    def test_filter_is_project_manager(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id) + "?is_project_manager=true")
        usernames = [m["username"] for m in response.json()["members"]]
        self.assertEqual(usernames, [self.pm_user.username])

    def test_unassigned_bot_excluded(self):
        # KippoOrganization.save() auto-creates an `unassigned-<slug>` membership.
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id))
        for member in response.json()["members"]:
            self.assertFalse(
                member["username"].startswith(settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX),
                f"unassigned bot leaked into members listing: {member['username']}",
            )

    def test_response_shape_has_all_fields(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id))
        pm = next(m for m in response.json()["members"] if m["username"] == self.pm_user.username)
        self.assertEqual(
            set(pm.keys()),
            {
                "user_id",
                "username",
                "display_name",
                "first_name",
                "last_name",
                "email",
                "github_login",
                "is_developer",
                "is_project_manager",
                "slack_username",
                "slack_user_id",
                "slack_image_url",
            },
        )
        # Slack/email fields come from OrganizationMembership, not KippoUser.
        self.assertEqual(pm["email"], "pat@github.com")
        self.assertEqual(pm["slack_username"], "pat")
        self.assertEqual(pm["slack_user_id"], "U02PM")
        self.assertEqual(pm["slack_image_url"], "https://example.com/pat.png")
        self.assertEqual(pm["first_name"], "Pat")
        self.assertEqual(pm["last_name"], "Manager")
        self.assertFalse(pm["is_developer"])
        self.assertTrue(pm["is_project_manager"])

    def test_superuser_can_list_any_org(self):
        superuser = KippoUser.objects.create(username="root", is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=superuser)
        response = self.client.get(self._url(self.other_organization.id))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        usernames = [m["username"] for m in response.json()["members"]]
        self.assertIn(self.other_org_user.username, usernames)
