"""Tests for GET /api/project-categories/ — kippo#43.

Read-only, org-scoped list of selectable project categories. Backs the kippo-ui
project create/edit form category picker (part of kippo#42).
"""

from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIClient

from projects.models import KippoProjectOrganizationCategory


class ProjectCategoryViewSetTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Another organization the user does NOT belong to.
        self.other_org = KippoOrganization.objects.create(
            name="other-org-for-category-test",
            github_organization_name="otherorg-category",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # Own-org category (active) and another active row; another-org category; an inactive own-org category.
        self.own_category = KippoProjectOrganizationCategory.objects.create(
            organization=self.organization,
            key="own-active",
            label="Own Active",
            sort_order=10,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_category = KippoProjectOrganizationCategory.objects.create(
            organization=self.other_org,
            key="other-active",
            label="Other Active",
            sort_order=10,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.inactive_category = KippoProjectOrganizationCategory.objects.create(
            organization=self.organization,
            key="own-inactive",
            label="Own Inactive",
            sort_order=20,
            is_active=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"{settings.URL_PREFIX}/api/project-categories/"

    def _keys(self, response: Response) -> set[str]:
        payload = response.json()
        rows = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
        return {row["key"] for row in rows}

    def test_unauthenticated_returns_401(self):
        anon = APIClient()
        response = anon.get(self.url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_lists_globals_and_own_org_but_not_other_org(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        keys = self._keys(response)
        # Global default category (seeded by migration, organization=null)
        self.assertIn("other", keys)
        # Own-org active category present
        self.assertIn("own-active", keys)
        # Another org's category is hidden
        self.assertNotIn("other-active", keys)

    def test_excludes_inactive_categories(self):
        response = self.client.get(self.url)
        keys = self._keys(response)
        self.assertNotIn("own-inactive", keys)

    def test_organization_filter_narrows_to_own_org_plus_globals(self):
        response = self.client.get(self.url, {"organization": str(self.organization.id)})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        keys = self._keys(response)
        self.assertIn("own-active", keys)
        self.assertIn("other", keys)  # globals always included
        self.assertNotIn("other-active", keys)

    def test_organization_filter_for_non_member_org_excludes_that_orgs_categories(self):
        response = self.client.get(self.url, {"organization": str(self.other_org.id)})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        keys = self._keys(response)
        # User is not a member of other_org — its category must not appear, globals still do.
        self.assertNotIn("other-active", keys)
        self.assertIn("other", keys)

    def test_response_shape(self):
        response = self.client.get(self.url)
        payload = response.json()
        rows = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
        sample = next(row for row in rows if row["key"] == "own-active")
        for field in ["id", "key", "label", "organization", "sort_order", "is_active"]:
            self.assertIn(field, sample)
        self.assertEqual(sample["organization"], str(self.organization.id))

    def test_superuser_sees_all_active_categories(self):
        superuser = KippoUser.objects.create(username="cat-superuser", email="su@example.com", is_superuser=True, is_staff=True)
        client = APIClient()
        client.force_authenticate(user=superuser)
        response = client.get(self.url)
        keys = self._keys(response)
        self.assertIn("own-active", keys)
        self.assertIn("other-active", keys)
        self.assertNotIn("own-inactive", keys)
