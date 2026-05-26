"""Tests for the octocat REST API (kippo#284)."""

from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase, override_settings
from projects.models import KippoProject
from rest_framework.test import APIClient

from ..models import GithubRepository


@override_settings(OCTOCAT_APPLY_DEFAULT_LABELSET=False)
class GithubRepositoryAPITestCase(TestCase):
    """Top-level + nested GithubRepository endpoints (kippo#284)."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        self.other_organization = KippoOrganization.objects.create(
            name="repo-api-other-org",
            github_organization_name="repo-api-other-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_user = KippoUser.objects.create(
            username="repo-api-otheruser",
            github_login="repo-api-otheruser",
            email="repo-api-otheruser@example.com",
            is_staff=True,
        )
        OrganizationMembership.objects.create(
            user=self.other_user,
            organization=self.other_organization,
            is_developer=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_project = KippoProject.objects.create(
            name="Other Org Project",
            organization=self.other_organization,
            columnset=self.project.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # Pre-existing repos: one linked to self.project, one in the other org
        self.own_repo = GithubRepository.objects.create(
            organization=self.organization,
            project=self.project,
            name="own-repo",
            api_url="https://api.github.com/repos/myorg/own-repo",
            html_url="https://github.com/myorg/own-repo",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_org_repo = GithubRepository.objects.create(
            organization=self.other_organization,
            project=self.other_project,
            name="other-org-repo",
            api_url="https://api.github.com/repos/repo-api-other-org/other-org-repo",
            html_url="https://github.com/repo-api-other-org/other-org-repo",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()

    # --- top-level /api/github-repositories/ -----------------------------

    def test_list_requires_authentication(self):
        url = f"{settings.URL_PREFIX}/api/github-repositories/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_list_is_org_scoped(self):
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/github-repositories/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = [r["id"] for r in response.json()["results"]]
        self.assertIn(str(self.own_repo.id), ids)
        self.assertNotIn(str(self.other_org_repo.id), ids)

    def test_retrieve_other_org_repo_returns_404(self):
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/github-repositories/{self.other_org_repo.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_superuser_sees_all_repos(self):
        superuser = KippoUser.objects.create(
            username="repo-api-superuser",
            github_login="repo-api-superuser",
            email="repo-api-superuser@example.com",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=superuser)
        url = f"{settings.URL_PREFIX}/api/github-repositories/"
        response = self.client.get(url)
        ids = [r["id"] for r in response.json()["results"]]
        self.assertIn(str(self.own_repo.id), ids)
        self.assertIn(str(self.other_org_repo.id), ids)

    # --- nested /api/projects/{pid}/github-repositories/ -----------------

    def _nested_url(self, project_id: object, pk: object | None = None) -> str:
        base = f"{settings.URL_PREFIX}/api/projects/{project_id}/github-repositories/"
        return f"{base}{pk}/" if pk else base

    def test_nested_list_filters_to_parent_project(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._nested_url(self.project.id))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = [r["id"] for r in response.json()["results"]]
        self.assertEqual(ids, [str(self.own_repo.id)])

    def test_nested_list_for_other_org_project_returns_403(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._nested_url(self.other_project.id))
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_nested_list_unknown_project_returns_404(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._nested_url("00000000-0000-4000-8000-000000000000"))
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_nested_create_new_repo_returns_201(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "name": "kiconiaworks-newrepo-api",
            "html_url": "https://github.com/myorg/kiconiaworks-newrepo-api",
            "api_url": "https://api.github.com/repos/myorg/kiconiaworks-newrepo-api",
        }
        response = self.client.post(self._nested_url(self.project.id), payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        repo = GithubRepository.objects.get(html_url=payload["html_url"])
        self.assertEqual(repo.organization, self.organization)
        self.assertEqual(repo.project, self.project)
        self.assertEqual(repo.created_by, self.user)

    def test_nested_create_existing_unlinked_repo_links_and_returns_200(self):
        # Repo exists but is not linked to any project
        repo = GithubRepository.objects.create(
            organization=self.organization,
            project=None,
            name="orphan-repo",
            api_url="https://api.github.com/repos/myorg/orphan-repo",
            html_url="https://github.com/myorg/orphan-repo",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_authenticate(user=self.user)
        payload = {"name": repo.name, "html_url": repo.html_url, "api_url": repo.api_url}
        response = self.client.post(self._nested_url(self.project.id), payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        repo.refresh_from_db()
        self.assertEqual(repo.project, self.project)
        self.assertEqual(GithubRepository.objects.filter(name="orphan-repo").count(), 1)

    def test_nested_create_already_linked_is_idempotent(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "name": self.own_repo.name,
            "html_url": self.own_repo.html_url,
            "api_url": self.own_repo.api_url,
        }
        response = self.client.post(self._nested_url(self.project.id), payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        # Still exactly one row
        self.assertEqual(GithubRepository.objects.filter(html_url=self.own_repo.html_url).count(), 1)

    def test_nested_create_cross_org_collision_returns_409(self):
        """Re-posting another org's existing repo to this project must not reparent (kippo#284 decision #3)."""
        self.client.force_authenticate(user=self.user)
        payload = {
            "name": self.other_org_repo.name,
            "html_url": self.other_org_repo.html_url,
            "api_url": self.other_org_repo.api_url,
        }
        response = self.client.post(self._nested_url(self.project.id), payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
        self.other_org_repo.refresh_from_db()
        self.assertEqual(self.other_org_repo.organization, self.other_organization)
        self.assertEqual(self.other_org_repo.project, self.other_project)

    def test_nested_create_for_other_org_project_returns_403(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "name": "wedge-repo",
            "html_url": "https://github.com/repo-api-other-org/wedge-repo",
            "api_url": "https://api.github.com/repos/repo-api-other-org/wedge-repo",
        }
        response = self.client.post(self._nested_url(self.other_project.id), payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)
        self.assertFalse(GithubRepository.objects.filter(name="wedge-repo").exists())

    def test_nested_delete_unlinks_but_keeps_row(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.delete(self._nested_url(self.project.id, self.own_repo.id))
        self.assertEqual(response.status_code, HTTPStatus.NO_CONTENT)
        self.own_repo.refresh_from_db()
        self.assertIsNone(self.own_repo.project)
        # Row persists
        self.assertTrue(GithubRepository.objects.filter(pk=self.own_repo.pk).exists())

    def test_nested_delete_repo_not_under_project_returns_404(self):
        # other_org_repo is not linked to self.project; deleting via self.project's path is 404
        # But we'd hit the org-scope 403 first because the user can't see self.project... wait,
        # the user CAN see self.project. The repo we name is under another project. The viewset's
        # get_queryset filters by project=self.project, so DRF returns 404.
        self.client.force_authenticate(user=self.user)
        # Create a repo in our org but linked to nothing
        loose = GithubRepository.objects.create(
            organization=self.organization,
            project=None,
            name="loose-repo",
            api_url="https://api.github.com/repos/myorg/loose-repo",
            html_url="https://github.com/myorg/loose-repo",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        response = self.client.delete(self._nested_url(self.project.id, loose.id))
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
