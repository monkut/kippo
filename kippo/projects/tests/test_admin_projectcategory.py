"""KippoProjectOrganizationCategoryAdmin permission scoping (kippo#49, option B).

Staff manage org-scoped categories; the global (organization=null) template is superuser-only —
mirroring the API rule (IsSuperuserOrOrgMemberForCategory).
"""

from accounts.models import KippoUser
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.test import RequestFactory, TestCase

from projects.admin import KippoProjectOrganizationCategoryAdmin
from projects.models import KippoProjectOrganizationCategory


class KippoProjectOrganizationCategoryAdminTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.staff_user = created["KippoUser"]  # is_staff=True, is_superuser=False
        self.assertTrue(self.staff_user.is_staff)
        self.assertFalse(self.staff_user.is_superuser)
        self.superuser = KippoUser.objects.create(username="cat-admin-su", email="su@example.com", is_staff=True, is_superuser=True)

        self.global_category = KippoProjectOrganizationCategory.objects.get(organization__isnull=True, key="other")
        self.org_category = KippoProjectOrganizationCategory.objects.get(organization=self.organization, key="other")

        self.admin = KippoProjectOrganizationCategoryAdmin(KippoProjectOrganizationCategory, admin.site)
        self.factory = RequestFactory()

    def _request(self, user: KippoUser) -> HttpRequest:
        request = self.factory.get("/")
        request.user = user
        return request

    def test_non_superuser_queryset_excludes_globals(self):
        rows = self.admin.get_queryset(self._request(self.staff_user))
        self.assertTrue(rows.exists())
        self.assertFalse(rows.filter(organization__isnull=True).exists())

    def test_superuser_queryset_includes_globals(self):
        rows = self.admin.get_queryset(self._request(self.superuser))
        self.assertTrue(rows.filter(organization__isnull=True).exists())

    def test_non_superuser_cannot_change_or_delete_global(self):
        request = self._request(self.staff_user)
        self.assertFalse(self.admin.has_change_permission(request, self.global_category))
        self.assertFalse(self.admin.has_delete_permission(request, self.global_category))

    def test_non_superuser_can_change_and_delete_org_row(self):
        request = self._request(self.staff_user)
        self.assertTrue(self.admin.has_change_permission(request, self.org_category))
        self.assertTrue(self.admin.has_delete_permission(request, self.org_category))

    def test_superuser_can_change_global(self):
        request = self._request(self.superuser)
        self.assertTrue(self.admin.has_change_permission(request, self.global_category))
        self.assertTrue(self.admin.has_delete_permission(request, self.global_category))

    def test_non_superuser_save_model_rejects_creating_a_global(self):
        request = self._request(self.staff_user)
        new_global = KippoProjectOrganizationCategory(organization=None, key="staff-global", label="Staff Global")
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(request, new_global, form=None, change=False)

    def test_superuser_save_model_allows_creating_a_global(self):
        request = self._request(self.superuser)
        new_global = KippoProjectOrganizationCategory(organization=None, key="su-global", label="SU Global")
        self.admin.save_model(request, new_global, form=None, change=False)
        self.assertTrue(KippoProjectOrganizationCategory.objects.filter(organization__isnull=True, key="su-global").exists())
