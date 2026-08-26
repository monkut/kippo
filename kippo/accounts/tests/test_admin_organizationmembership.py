from unittest.mock import MagicMock, patch

from commons.tests import IsStaffModelAdminTestCaseBase, MockRequest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied
from django.forms import ModelChoiceField
from django.urls import reverse

from ..admin import OrganizationMembershipAdmin
from ..models import KippoOrganization, KippoUser, OrganizationMembership


class OrganizationMembershipAdminTestCaseBase(IsStaffModelAdminTestCaseBase):
    def setUp(self):
        super().setUp()
        self.site = AdminSite()
        self.modeladmin = OrganizationMembershipAdmin(OrganizationMembership, self.site)

        # administers self.organization only
        self.single_org_admin = KippoUser.objects.create(username="membership_single_org_admin", is_superuser=False, is_staff=True)
        self._add_membership(self.single_org_admin, self.organization, is_admin=True)

        # administers self.organization, plainly belongs to self.other_organization -- the case where
        # membership-based scoping would hand out is_admin in an unadministered organization.
        self.dualrole_user = KippoUser.objects.create(username="membership_admin_and_plain_member", is_superuser=False, is_staff=True)
        self._add_membership(self.dualrole_user, self.organization, is_admin=True)
        self._add_membership(self.dualrole_user, self.other_organization, is_admin=False)

        # administers both organizations -- keeps a live (scoped) selector
        self.multi_org_admin = KippoUser.objects.create(username="membership_multi_org_admin", is_superuser=False, is_staff=True)
        self._add_membership(self.multi_org_admin, self.organization, is_admin=True)
        self._add_membership(self.multi_org_admin, self.other_organization, is_admin=True)

        # project manager WITHOUT the role -- proves PM alone grants nothing here
        self.pm_user = KippoUser.objects.create(username="membership_pm_without_role", is_superuser=False, is_staff=True)
        self._add_membership(self.pm_user, self.organization, is_admin=False, is_project_manager=True)

        # the user a membership is created *for*
        self.target_user = KippoUser.objects.create(username="membership_target", is_superuser=False, is_staff=True)

    def _add_membership(self, user: KippoUser, organization: KippoOrganization, **kwargs) -> OrganizationMembership:
        return OrganizationMembership.objects.create(
            user=user,
            organization=organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            **kwargs,
        )

    def _request_for(self, user: KippoUser) -> MockRequest:
        request = MockRequest()
        request.user = user
        return request


class OrganizationMembershipAdminPermissionTestCase(OrganizationMembershipAdminTestCaseBase):
    """Only superusers and organization admins may read or write memberships (kiconiaworks/kippo#57)."""

    def test_superuser_may_view_add_and_change(self):
        self.assertTrue(self.modeladmin.has_view_permission(self.super_user_request))
        self.assertTrue(self.modeladmin.has_add_permission(self.super_user_request))
        self.assertTrue(self.modeladmin.has_change_permission(self.super_user_request))

    def test_organization_admin_may_view_add_and_change(self):
        request = self._request_for(self.single_org_admin)
        self.assertTrue(self.modeladmin.has_view_permission(request))
        self.assertTrue(self.modeladmin.has_add_permission(request))
        self.assertTrue(self.modeladmin.has_change_permission(request))

    def test_project_manager_without_role_may_not_view_add_or_change(self):
        request = self._request_for(self.pm_user)
        self.assertFalse(self.modeladmin.has_view_permission(request))
        self.assertFalse(self.modeladmin.has_add_permission(request))
        self.assertFalse(self.modeladmin.has_change_permission(request))

    def test_plain_member_may_not_view_add_or_change(self):
        self.assertFalse(self.modeladmin.has_view_permission(self.staff_user_request))
        self.assertFalse(self.modeladmin.has_add_permission(self.staff_user_request))
        self.assertFalse(self.modeladmin.has_change_permission(self.staff_user_request))

    def test_staff_user_with_no_organization_may_not_view(self):
        self.assertFalse(self.modeladmin.has_view_permission(self.staff_user2_request))

    def test_delete_remains_superuser_only(self):
        membership = self._add_membership(self.target_user, self.organization)
        self.assertTrue(self.modeladmin.has_delete_permission(self.super_user_request, membership))
        # an organization admin administers this organization and may still not delete
        self.assertFalse(self.modeladmin.has_delete_permission(self._request_for(self.single_org_admin), membership))
        self.assertFalse(self.modeladmin.has_delete_permission(self._request_for(self.single_org_admin)))

    def test_change_permission_scoped_to_administered_organization(self):
        own_membership = self._add_membership(self.target_user, self.organization)
        other_membership = self._add_membership(self.target_user, self.other_organization)

        request = self._request_for(self.dualrole_user)
        self.assertTrue(self.modeladmin.has_change_permission(request, own_membership))
        # merely belonging to other_organization must not grant write access to its memberships
        self.assertFalse(self.modeladmin.has_change_permission(request, other_membership))

    def test_queryset_scoped_to_administered_organizations(self):
        own_membership = self._add_membership(self.target_user, self.organization)
        other_membership = self._add_membership(self.target_user, self.other_organization)

        visible = set(self.modeladmin.get_queryset(self._request_for(self.dualrole_user)).values_list("id", flat=True))
        self.assertIn(own_membership.id, visible)
        self.assertNotIn(other_membership.id, visible)

    def test_queryset_unrestricted_for_superuser(self):
        own_membership = self._add_membership(self.target_user, self.organization)
        other_membership = self._add_membership(self.target_user, self.other_organization)

        visible = set(self.modeladmin.get_queryset(self.super_user_request).values_list("id", flat=True))
        self.assertIn(own_membership.id, visible)
        self.assertIn(other_membership.id, visible)

    def test_queryset_empty_for_member_without_admin_role(self):
        self._add_membership(self.target_user, self.organization)
        self.assertEqual(list(self.modeladmin.get_queryset(self._request_for(self.pm_user))), [])

    @patch.object(OrganizationMembershipAdmin, "message_user")
    def test_save_model_rejects_an_unadministered_organization(self, mock_message_user: MagicMock):
        membership = OrganizationMembership(
            user=self.target_user,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        with self.assertRaises(PermissionDenied):
            self.modeladmin.save_model(self._request_for(self.dualrole_user), membership, form=None, change=False)
        self.assertFalse(OrganizationMembership.objects.filter(user=self.target_user, organization=self.other_organization).exists())

    @patch.object(OrganizationMembershipAdmin, "message_user")
    def test_save_model_accepts_an_administered_organization(self, mock_message_user: MagicMock):
        membership = OrganizationMembership(
            user=self.target_user,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.modeladmin.save_model(self._request_for(self.single_org_admin), membership, form=None, change=False)
        self.assertTrue(OrganizationMembership.objects.filter(pk=membership.pk).exists())
        self.assertEqual(membership.created_by, self.single_org_admin)


class OrganizationMembershipAdminOrganizationFieldTestCase(OrganizationMembershipAdminTestCaseBase):
    """The organization selector: scoped for multi-organization admins, fixed for single-organization ones."""

    def _organization_formfield(self, user: KippoUser) -> ModelChoiceField:
        db_field = OrganizationMembership._meta.get_field("organization")
        return self.modeladmin.formfield_for_foreignkey(db_field, self._request_for(user))

    def test_single_organization_admin_gets_a_prefilled_locked_field(self):
        formfield = self._organization_formfield(self.single_org_admin)
        self.assertTrue(formfield.disabled)
        self.assertEqual(formfield.initial, self.organization.pk)
        self.assertEqual(list(formfield.queryset), [self.organization])

    def test_dual_role_admin_counts_only_administered_organizations_as_one(self):
        # belongs to two organizations but administers one -- still a single-organization admin here
        formfield = self._organization_formfield(self.dualrole_user)
        self.assertTrue(formfield.disabled)
        self.assertEqual(formfield.initial, self.organization.pk)
        self.assertEqual(list(formfield.queryset), [self.organization])

    def test_multi_organization_admin_keeps_a_live_scoped_selector(self):
        formfield = self._organization_formfield(self.multi_org_admin)
        self.assertFalse(formfield.disabled)
        choices = set(formfield.queryset.values_list("id", flat=True))
        self.assertEqual(choices, {self.organization.id, self.other_organization.id})

    def test_superuser_selector_is_unrestricted_and_editable(self):
        db_field = OrganizationMembership._meta.get_field("organization")
        formfield = self.modeladmin.formfield_for_foreignkey(db_field, self.super_user_request)
        self.assertFalse(formfield.disabled)
        choices = set(formfield.queryset.values_list("id", flat=True))
        self.assertIn(self.organization.id, choices)
        self.assertIn(self.other_organization.id, choices)


class OrganizationMembershipAdminHttpTestCase(OrganizationMembershipAdminTestCaseBase):
    """The admin views over HTTP -- the gates the ModelAdmin-level tests reach past."""

    def setUp(self):
        super().setUp()
        self.add_url = reverse("admin:accounts_organizationmembership_add")
        self.changelist_url = reverse("admin:accounts_organizationmembership_changelist")

    def test_single_organization_admin_add_ignores_a_forged_organization(self):
        # the field is disabled, so Django cleans it to `initial` and discards whatever was posted
        self.client.force_login(self.dualrole_user)
        response = self.client.post(
            self.add_url,
            {"user": str(self.target_user.pk), "organization": str(self.other_organization.pk)},
        )
        self.assertEqual(response.status_code, 302)
        membership = OrganizationMembership.objects.get(user=self.target_user)
        self.assertEqual(membership.organization, self.organization)
        self.assertEqual(membership.created_by, self.dualrole_user)

    def test_single_organization_admin_add_succeeds_without_posting_an_organization(self):
        self.client.force_login(self.single_org_admin)
        response = self.client.post(self.add_url, {"user": str(self.target_user.pk)})
        self.assertEqual(response.status_code, 302)
        membership = OrganizationMembership.objects.get(user=self.target_user)
        self.assertEqual(membership.organization, self.organization)

    def test_multi_organization_admin_add_rejects_an_unadministered_organization(self):
        # a live selector is forgeable, so the scoped queryset must reject the pk at validation
        third_organization = KippoOrganization.objects.create(
            name="third-test-organization",
            github_organization_name="membership-third-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_login(self.multi_org_admin)
        response = self.client.post(
            self.add_url,
            {"user": str(self.target_user.pk), "organization": str(third_organization.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("organization", response.context["adminform"].form.errors)
        # KippoOrganization.save() auto-creates an "unassigned" membership, so scope to the posted user
        self.assertFalse(OrganizationMembership.objects.filter(user=self.target_user, organization=third_organization).exists())

    def test_changelist_omits_a_joined_but_unadministered_organization(self):
        own_membership = self._add_membership(self.target_user, self.organization)
        other_membership = self._add_membership(self.target_user, self.other_organization)

        self.client.force_login(self.dualrole_user)
        response = self.client.get(self.changelist_url)
        self.assertEqual(response.status_code, 200)
        listed = set(response.context["cl"].queryset.values_list("id", flat=True))
        self.assertIn(own_membership.id, listed)
        self.assertNotIn(other_membership.id, listed)

    def test_changelist_is_forbidden_for_a_member_without_the_role(self):
        self.client.force_login(self.pm_user)
        response = self.client.get(self.changelist_url)
        self.assertEqual(response.status_code, 403)

    def test_change_view_cannot_load_an_unadministered_organizations_membership(self):
        other_membership = self._add_membership(self.target_user, self.other_organization)
        change_url = reverse("admin:accounts_organizationmembership_change", args=[other_membership.pk])

        self.client.force_login(self.dualrole_user)
        response = self.client.get(change_url)
        # get_object() loads through the scoped get_queryset, so the object is unreachable
        self.assertIn(response.status_code, (302, 403, 404))
        self.assertTrue(OrganizationMembership.objects.filter(pk=other_membership.pk).exists())
