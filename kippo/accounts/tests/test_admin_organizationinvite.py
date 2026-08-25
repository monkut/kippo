from unittest.mock import MagicMock, patch

from commons.tests import IsStaffModelAdminTestCaseBase, MockRequest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied

from ..admin import OrganizationInviteAdmin
from ..models import KippoOrganization, KippoUser, OrganizationInvite, OrganizationMembership


class OrganizationInviteAdminPermissionTestCase(IsStaffModelAdminTestCaseBase):
    """Organization admins may manage invites for the organizations they administer (kiconiaworks/kippo#57)."""

    def setUp(self):
        super().setUp()
        self.site = AdminSite()
        self.modeladmin = OrganizationInviteAdmin(OrganizationInvite, self.site)

        # an organization admin of self.organization only
        self.orgadmin_user = KippoUser.objects.create(username="orgadmin_with_org", is_superuser=False, is_staff=True)
        OrganizationMembership.objects.create(
            user=self.orgadmin_user,
            organization=self.organization,
            is_admin=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.orgadmin_request = MockRequest()
        self.orgadmin_request.user = self.orgadmin_user

        # a project manager WITHOUT the org-admin role -- proves PM alone does not grant invite access
        self.pm_user = KippoUser.objects.create(username="pm_without_orgadmin", is_superuser=False, is_staff=True)
        OrganizationMembership.objects.create(
            user=self.pm_user,
            organization=self.organization,
            is_project_manager=True,
            is_admin=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.pm_request = MockRequest()
        self.pm_request.user = self.pm_user

        # admin of self.organization AND a plain member of self.other_organization -- the case
        # where membership-based scoping would leak the second org's invites.
        self.dualrole_user = KippoUser.objects.create(username="orgadmin_and_plain_member", is_superuser=False, is_staff=True)
        OrganizationMembership.objects.create(
            user=self.dualrole_user,
            organization=self.organization,
            is_admin=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        OrganizationMembership.objects.create(
            user=self.dualrole_user,
            organization=self.other_organization,
            is_admin=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.dualrole_request = MockRequest()
        self.dualrole_request.user = self.dualrole_user

    def _build_invite(self, organization: KippoOrganization, email: str = "invitee@testorg.com") -> OrganizationInvite:
        return OrganizationInvite(
            organization=organization,
            email=email,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_superuser_may_add(self):
        self.assertTrue(self.modeladmin.has_add_permission(self.super_user_request))

    def test_organization_admin_may_add(self):
        self.assertTrue(self.modeladmin.has_add_permission(self.orgadmin_request))

    def test_project_manager_without_role_may_not_add(self):
        # is_project_manager is deliberately NOT sufficient -- invites can promote is_staff
        # via the organization's staff EmailDomain.
        self.assertFalse(self.modeladmin.has_add_permission(self.pm_request))

    def test_plain_member_may_not_add(self):
        self.assertFalse(self.modeladmin.has_add_permission(self.staff_user_request))

    def test_plain_member_write_permissions_unchanged(self):
        self.assertFalse(self.modeladmin.has_change_permission(self.staff_user_request))
        self.assertFalse(self.modeladmin.has_delete_permission(self.staff_user_request))

    def test_organization_admin_has_view_permission(self):
        # without the has_view_permission override an org admin would need an explicit
        # accounts.view_organizationinvite grant to reach the changelist.
        self.assertTrue(self.modeladmin.has_view_permission(self.orgadmin_request))

    def test_organization_field_choices_scoped_to_administered_organizations(self):
        db_field = OrganizationInvite._meta.get_field("organization")
        formfield = self.modeladmin.formfield_for_foreignkey(db_field, self.orgadmin_request)
        choices = set(formfield.queryset.values_list("id", flat=True))
        self.assertIn(self.organization.id, choices)
        self.assertNotIn(self.other_organization.id, choices)

    def test_organization_field_choices_unrestricted_for_superuser(self):
        db_field = OrganizationInvite._meta.get_field("organization")
        formfield = self.modeladmin.formfield_for_foreignkey(db_field, self.super_user_request)
        choices = set(formfield.queryset.values_list("id", flat=True))
        self.assertIn(self.organization.id, choices)
        self.assertIn(self.other_organization.id, choices)

    def test_organization_admin_may_change_and_delete_own_organization_invite(self):
        invite = self._build_invite(self.organization)
        invite.save()
        self.assertTrue(self.modeladmin.has_change_permission(self.orgadmin_request, invite))
        self.assertTrue(self.modeladmin.has_delete_permission(self.orgadmin_request, invite))

    def test_organization_admin_may_not_change_or_delete_other_organization_invite(self):
        invite = self._build_invite(self.other_organization, email="invitee@othertestorg.com")
        invite.save()
        self.assertFalse(self.modeladmin.has_change_permission(self.orgadmin_request, invite))
        self.assertFalse(self.modeladmin.has_delete_permission(self.orgadmin_request, invite))

    @patch.object(OrganizationInviteAdmin, "message_user")
    def test_save_model_creates_invite_for_administered_organization(self, mock_message_user: MagicMock):
        invite = self._build_invite(self.organization)
        self.modeladmin.save_model(self.orgadmin_request, invite, form=None, change=False)
        self.assertTrue(OrganizationInvite.objects.filter(pk=invite.pk, organization=self.organization).exists())
        self.assertEqual(invite.created_by, self.orgadmin_user)

    @patch.object(OrganizationInviteAdmin, "message_user")
    def test_save_model_rejects_unadministered_organization(self, mock_message_user: MagicMock):
        # a forged POST bypasses the scoped `organization` choices, so save_model re-checks.
        invite = self._build_invite(self.other_organization, email="invitee@othertestorg.com")
        with self.assertRaises(PermissionDenied):
            self.modeladmin.save_model(self.orgadmin_request, invite, form=None, change=False)
        self.assertFalse(OrganizationInvite.objects.filter(organization=self.other_organization).exists())

    @patch.object(OrganizationInviteAdmin, "message_user")
    def test_save_model_allows_superuser_any_organization(self, mock_message_user: MagicMock):
        invite = self._build_invite(self.other_organization, email="invitee@othertestorg.com")
        self.modeladmin.save_model(self.super_user_request, invite, form=None, change=False)
        self.assertTrue(OrganizationInvite.objects.filter(pk=invite.pk).exists())

    def test_queryset_scoped_to_administered_organizations(self):
        own_invite = self._build_invite(self.organization)
        own_invite.save()
        other_invite = self._build_invite(self.other_organization, email="invitee@othertestorg.com")
        other_invite.save()

        visible = set(self.modeladmin.get_queryset(self.orgadmin_request).values_list("id", flat=True))
        self.assertIn(own_invite.id, visible)
        self.assertNotIn(other_invite.id, visible)

        superuser_visible = set(self.modeladmin.get_queryset(self.super_user_request).values_list("id", flat=True))
        self.assertIn(own_invite.id, superuser_visible)
        self.assertIn(other_invite.id, superuser_visible)

    def test_queryset_hides_invites_of_a_joined_but_unadministered_organization(self):
        # membership alone must not expose the other org's invitee email addresses.
        own_invite = self._build_invite(self.organization)
        own_invite.save()
        other_invite = self._build_invite(self.other_organization, email="invitee@othertestorg.com")
        other_invite.save()

        visible = set(self.modeladmin.get_queryset(self.dualrole_request).values_list("id", flat=True))
        self.assertIn(own_invite.id, visible)
        self.assertNotIn(other_invite.id, visible)

    def test_queryset_empty_for_member_without_admin_role(self):
        invite = self._build_invite(self.organization)
        invite.save()
        self.assertEqual(list(self.modeladmin.get_queryset(self.pm_request)), [])

    def test_joined_but_unadministered_organization_invite_is_not_writable(self):
        # second gate: even if the object were reachable, the object-level permission denies.
        other_invite = self._build_invite(self.other_organization, email="invitee@othertestorg.com")
        other_invite.save()
        self.assertFalse(self.modeladmin.has_change_permission(self.dualrole_request, other_invite))
        self.assertFalse(self.modeladmin.has_delete_permission(self.dualrole_request, other_invite))

    def test_organization_field_choices_exclude_joined_but_unadministered_organization(self):
        db_field = OrganizationInvite._meta.get_field("organization")
        formfield = self.modeladmin.formfield_for_foreignkey(db_field, self.dualrole_request)
        choices = set(formfield.queryset.values_list("id", flat=True))
        self.assertIn(self.organization.id, choices)
        self.assertNotIn(self.other_organization.id, choices)

    @patch.object(OrganizationInviteAdmin, "message_user")
    def test_save_model_rejects_joined_but_unadministered_organization(self, mock_message_user: MagicMock):
        invite = self._build_invite(self.other_organization, email="invitee@othertestorg.com")
        with self.assertRaises(PermissionDenied):
            self.modeladmin.save_model(self.dualrole_request, invite, form=None, change=False)
        self.assertFalse(OrganizationInvite.objects.filter(organization=self.other_organization).exists())


class OrganizationAdminRoleModelTestCase(IsStaffModelAdminTestCaseBase):
    """KippoUser helpers backing the OrganizationMembership.is_admin role."""

    def setUp(self):
        super().setUp()
        self.orgadmin_user = KippoUser.objects.create(username="orgadmin_role_user", is_superuser=False, is_staff=True)
        OrganizationMembership.objects.create(
            user=self.orgadmin_user,
            organization=self.organization,
            is_admin=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_admin_organizations_lists_only_administered(self):
        organizations = list(self.orgadmin_user.admin_organizations)
        self.assertEqual(organizations, [self.organization])

    def test_admin_organizations_empty_for_plain_member(self):
        self.assertEqual(list(self.staffuser_with_org.admin_organizations), [])

    def test_is_organization_admin_of(self):
        self.assertTrue(self.orgadmin_user.is_organization_admin_of(self.organization))
        self.assertFalse(self.orgadmin_user.is_organization_admin_of(self.other_organization))

    def test_is_organization_admin_of_superuser_administers_all(self):
        self.assertTrue(self.superuser_no_org.is_organization_admin_of(self.organization))
        self.assertTrue(self.superuser_no_org.is_organization_admin_of(self.other_organization))

    def test_membership_role_defaults_false(self):
        membership = self.staffuser_with_org.get_membership(self.organization)
        self.assertFalse(membership.is_admin)
