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


class ProjectCategoryWriteViewSetTestCase(TestCase):
    """Org-member management (create/update/delete) of project categories — kippo#48."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.created = setup_basic_project()
        self.organization = self.created["KippoOrganization"]
        self.user = self.created["KippoUser"]  # a member (is_developer) of self.organization
        self.github_manager = KippoUser.objects.get(username="github-manager")

        self.other_org = KippoOrganization.objects.create(
            name="other-org-for-category-write-test",
            github_organization_name="otherorg-category-write",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
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
        self.global_category = KippoProjectOrganizationCategory.objects.get(organization__isnull=True, key="other")

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"{settings.URL_PREFIX}/api/project-categories/"

    def _detail_url(self, category: KippoProjectOrganizationCategory) -> str:
        return f"{self.url}{category.id}/"

    # --- create ---
    def test_member_can_create_own_org_category(self):
        # self.user is a member (is_developer) of self.organization — no PM role required.
        response = self.client.post(
            self.url,
            {"organization": str(self.organization.id), "key": "new-cat", "label": "New Category", "sort_order": 5},
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        created = KippoProjectOrganizationCategory.objects.get(organization=self.organization, key="new-cat")
        # audit user recorded
        self.assertEqual(created.created_by, self.user)
        self.assertEqual(created.updated_by, self.user)

    def test_non_member_cannot_create(self):
        outsider = KippoUser.objects.create(username="cat-outsider", email="outsider@example.com")
        client = APIClient()
        client.force_authenticate(user=outsider)
        response = client.post(
            self.url,
            {"organization": str(self.organization.id), "key": "nope", "label": "Nope"},
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_member_cannot_create_for_other_org(self):
        response = self.client.post(
            self.url,
            {"organization": str(self.other_org.id), "key": "cross-org", "label": "Cross Org"},
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_member_cannot_create_global_category(self):
        response = self.client.post(
            self.url,
            {"key": "member-global", "label": "Member Global"},
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_superuser_can_create_global_category(self):
        superuser = KippoUser.objects.create(username="cat-write-su", email="su2@example.com", is_superuser=True, is_staff=True)
        client = APIClient()
        client.force_authenticate(user=superuser)
        response = client.post(self.url, {"key": "su-global", "label": "SU Global"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        self.assertTrue(KippoProjectOrganizationCategory.objects.filter(organization__isnull=True, key="su-global").exists())

    def test_duplicate_org_key_rejected_with_400_not_500(self):
        response = self.client.post(
            self.url,
            {"organization": str(self.organization.id), "key": "own-active", "label": "Dup Key"},
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)

    def test_cross_scope_label_collision_rejected(self):
        response = self.client.post(
            self.url,
            {"organization": str(self.organization.id), "key": "shadow-global", "label": self.global_category.label},
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST, response.content)

    # --- update ---
    def test_member_can_update_own_org_category(self):
        response = self.client.patch(self._detail_url(self.own_category), {"label": "Renamed"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK, response.content)
        self.own_category.refresh_from_db()
        self.assertEqual(self.own_category.label, "Renamed")
        self.assertEqual(self.own_category.updated_by, self.user)

    def test_member_cannot_update_other_org_category(self):
        response = self.client.patch(self._detail_url(self.other_category), {"label": "Hijack"}, format="json")
        # other_org category is outside the user's queryset -> 404 (not leaked as 403)
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_member_cannot_update_global_category(self):
        response = self.client.patch(self._detail_url(self.global_category), {"label": "Hijack Global"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    # --- delete ---
    def test_member_can_delete_unused_own_org_category(self):
        response = self.client.delete(self._detail_url(self.own_category))
        self.assertEqual(response.status_code, HTTPStatus.NO_CONTENT)
        self.assertFalse(KippoProjectOrganizationCategory.objects.filter(pk=self.own_category.pk).exists())

    def test_delete_in_use_category_returns_409(self):
        # Attach the category to an existing (fully-formed) project so the delete hits PROTECT.
        project = self.created["KippoProject"]
        project.category = self.own_category
        project.save()
        response = self.client.delete(self._detail_url(self.own_category))
        self.assertEqual(response.status_code, HTTPStatus.CONFLICT, response.content)
        self.assertTrue(KippoProjectOrganizationCategory.objects.filter(pk=self.own_category.pk).exists())

    def test_member_cannot_delete_global_category(self):
        response = self.client.delete(self._detail_url(self.global_category))
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    # --- inactive visibility (soft-delete / reactivation) ---
    def test_default_list_hides_inactive_but_include_inactive_shows_it(self):
        inactive = KippoProjectOrganizationCategory.objects.create(
            organization=self.organization,
            key="own-inactive",
            label="Own Inactive",
            is_active=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        default_keys = {row["key"] for row in self.client.get(self.url).json()["results"]}
        self.assertNotIn(inactive.key, default_keys)
        with_inactive_keys = {row["key"] for row in self.client.get(self.url, {"include_inactive": "true"}).json()["results"]}
        self.assertIn(inactive.key, with_inactive_keys)

    def test_member_can_reactivate_inactive_category(self):
        inactive = KippoProjectOrganizationCategory.objects.create(
            organization=self.organization,
            key="reactivate-me",
            label="Reactivate Me",
            is_active=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # detail actions must reach inactive rows even though the default list hides them
        response = self.client.patch(self._detail_url(inactive), {"is_active": True}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK, response.content)
        inactive.refresh_from_db()
        self.assertTrue(inactive.is_active)
