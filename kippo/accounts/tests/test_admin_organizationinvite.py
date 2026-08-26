from unittest.mock import MagicMock, patch

from commons.tests import IsStaffModelAdminTestCaseBase, MockRequest
from django.contrib import admin as django_admin
from django.contrib.admin import helpers
from django.contrib.admin.actions import delete_selected
from django.contrib.admin.sites import AdminSite
from django.contrib.admin.templatetags.admin_list import result_headers
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse

from ..admin import OrganizationInviteAdmin
from ..models import KippoOrganization, KippoUser, OrganizationInvite, OrganizationMembership


class OrganizationInviteAdminPermissionTestCase(IsStaffModelAdminTestCaseBase):
    """Organization admins may manage invites for the organizations they administer (kiconiaworks/kippo#57)."""

    def setUp(self):
        super().setUp()
        self.site = AdminSite()
        self.modeladmin = OrganizationInviteAdmin(OrganizationInvite, self.site)
        # the instance actually serving /admin/ -- its admin_site registry is what
        # get_deleted_objects consults when collecting per-object delete permissions.
        self.registered_modeladmin = django_admin.site._registry[OrganizationInvite]

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

    def test_view_permission_falls_back_to_django_permissions_for_non_administrators(self):
        # has_view_permission short-circuits to True only for organization admins; everyone else
        # falls through to ModelAdmin.has_view_permission, which requires an explicit
        # accounts.view_organizationinvite (or change) grant that none of these users hold.
        self.assertFalse(self.modeladmin.has_view_permission(self.pm_request))
        self.assertFalse(self.modeladmin.has_view_permission(self.staff_user_request))
        self.assertFalse(self.modeladmin.has_view_permission(self.staff_user2_request))

    def test_bulk_delete_selected_denies_an_unadministered_organization_invite(self):
        # get_queryset already keeps this object out of the changelist, so reaching the action with
        # it selected should be impossible -- this asserts the *second* gate independently, by
        # handing delete_selected a queryset that scoping would never produce.
        #
        # This must run against the ModelAdmin registered on the real admin site, not
        # `self.modeladmin`: get_deleted_objects only consults has_delete_permission for models
        # found in `admin_site._registry` (contrib/admin/utils.py format_callback), so against the
        # bare AdminSite() built in setUp the per-object check is skipped and nothing is denied.
        other_invite = self._build_invite(self.other_organization, email="invitee@othertestorg.com")
        other_invite.save()

        request = RequestFactory().post("/admin/accounts/organizationinvite/", {"post": "yes"})
        request.user = self.dualrole_user

        with self.assertRaises(PermissionDenied):
            delete_selected(self.registered_modeladmin, request, OrganizationInvite.objects.filter(pk=other_invite.pk))
        self.assertTrue(OrganizationInvite.objects.filter(pk=other_invite.pk).exists())

    def test_bulk_delete_selected_allows_an_administered_organization_invite(self):
        # proves the denial above is the permission check, not a broken delete_selected call.
        own_invite = self._build_invite(self.organization)
        own_invite.save()

        request = RequestFactory().post("/admin/accounts/organizationinvite/", {"post": "yes"})
        request.user = self.dualrole_user

        with patch.object(OrganizationInviteAdmin, "message_user"):
            deleted = delete_selected(self.registered_modeladmin, request, OrganizationInvite.objects.filter(pk=own_invite.pk))
        self.assertIsNone(deleted)
        self.assertFalse(OrganizationInvite.objects.filter(pk=own_invite.pk).exists())


class OrganizationInviteAdminHttpTestCase(IsStaffModelAdminTestCaseBase):
    """The admin views over HTTP -- the gates the ModelAdmin-level tests reach past.

    The `organization` selector is not cosmetic: `ModelChoiceField.to_python` resolves the
    submitted pk against the scoped queryset and raises `invalid_choice` on a miss, so a forged
    POST fails form validation before `save_model` is ever consulted. That gate has no other test.
    """

    def setUp(self):
        super().setUp()
        # administers self.organization, plainly belongs to self.other_organization
        self.orgadmin_user = KippoUser.objects.create(username="orgadmin_http", is_superuser=False, is_staff=True)
        OrganizationMembership.objects.create(
            user=self.orgadmin_user,
            organization=self.organization,
            is_admin=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        OrganizationMembership.objects.create(
            user=self.orgadmin_user,
            organization=self.other_organization,
            is_admin=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_login(self.orgadmin_user)
        self.add_url = reverse("admin:accounts_organizationinvite_add")
        self.changelist_url = reverse("admin:accounts_organizationinvite_changelist")

    def _create_invite(self, organization: KippoOrganization, email: str) -> OrganizationInvite:
        return OrganizationInvite.objects.create(
            organization=organization,
            email=email,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_add_view_rejects_a_forged_unadministered_organization_pk(self):
        response = self.client.post(
            self.add_url,
            {"organization": str(self.other_organization.pk), "email": "invitee@othertestorg.com"},
        )
        # the form re-renders with an error rather than redirecting to the changelist
        self.assertEqual(response.status_code, 200)
        self.assertIn("organization", response.context["adminform"].form.errors)
        self.assertFalse(OrganizationInvite.objects.filter(organization=self.other_organization).exists())

    def test_add_view_creates_an_invite_for_an_administered_organization(self):
        response = self.client.post(
            self.add_url,
            {"organization": str(self.organization.pk), "email": "invitee@testorg.com"},
        )
        self.assertEqual(response.status_code, 302)
        invite = OrganizationInvite.objects.get(organization=self.organization, email="invitee@testorg.com")
        self.assertEqual(invite.created_by, self.orgadmin_user)

    def test_changelist_omits_a_joined_but_unadministered_organization_invite(self):
        own_invite = self._create_invite(self.organization, "invitee@testorg.com")
        other_invite = self._create_invite(self.other_organization, "invitee@othertestorg.com")

        response = self.client.get(self.changelist_url)
        self.assertEqual(response.status_code, 200)
        listed = set(response.context["cl"].queryset.values_list("id", flat=True))
        self.assertIn(own_invite.id, listed)
        self.assertNotIn(other_invite.id, listed)
        self.assertNotContains(response, "invitee@othertestorg.com")

    def test_bulk_delete_action_cannot_reach_an_unadministered_organization_invite(self):
        other_invite = self._create_invite(self.other_organization, "invitee@othertestorg.com")

        response = self.client.post(
            self.changelist_url,
            {
                "action": "delete_selected",
                helpers.ACTION_CHECKBOX_NAME: [str(other_invite.pk)],
                "post": "yes",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(OrganizationInvite.objects.filter(pk=other_invite.pk).exists())

    def test_bulk_delete_action_deletes_an_administered_organization_invite(self):
        own_invite = self._create_invite(self.organization, "invitee@testorg.com")

        response = self.client.post(
            self.changelist_url,
            {
                "action": "delete_selected",
                helpers.ACTION_CHECKBOX_NAME: [str(own_invite.pk)],
                "post": "yes",
            },
        )
        # delete_selected returns None after deleting, so response_action redirects to the changelist
        self.assertEqual(response.status_code, 302)
        self.assertFalse(OrganizationInvite.objects.filter(pk=own_invite.pk).exists())


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


class OrganizationInviteAdminJapaneseLabelTestCase(IsStaffModelAdminTestCaseBase):
    """Entries are labeled in Japanese -- changelist column headers and change-form field labels."""

    CHANGELIST_HEADERS = ("組織", "メールアドレス", "有効期限", "処理済み", "処理日時")

    def setUp(self):
        super().setUp()
        self.invite = OrganizationInvite.objects.create(
            organization=self.organization,
            email=f"invitee@{self.organization_domain}",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_login(self.superuser_no_org)

    def test_changelist_column_headers_are_japanese(self):
        response = self.client.get(reverse("admin:accounts_organizationinvite_changelist"))
        self.assertEqual(response.status_code, 200)
        # compare against the per-column header labels, NOT the raw page HTML: 組織 is a substring
        # of the 組織招待 page title, so a whole-page containment check would pass regardless.
        headers = [str(header["text"]) for header in result_headers(response.context["cl"])]
        for header in self.CHANGELIST_HEADERS:
            self.assertIn(header, headers)

    def test_changelist_does_not_repeat_a_column(self):
        response = self.client.get(reverse("admin:accounts_organizationinvite_changelist"))
        self.assertEqual(response.status_code, 200)
        headers = [str(header["text"]) for header in result_headers(response.context["cl"])]
        self.assertEqual(len(headers), len(set(headers)))

    def test_change_form_field_labels_are_japanese(self):
        change_url = reverse("admin:accounts_organizationinvite_change", args=[self.invite.pk])
        response = self.client.get(change_url)
        self.assertEqual(response.status_code, 200)
        labels = {name: str(field.label) for name, field in response.context["adminform"].form.fields.items()}
        # expiration_date/is_complete/processed_datetime are editable=False, so the form carries
        # only these two; their headers are covered by the changelist test above.
        self.assertEqual(labels["organization"], "組織")
        self.assertEqual(labels["email"], "メールアドレス")

    def test_model_verbose_name_is_japanese(self):
        self.assertEqual(str(OrganizationInvite._meta.verbose_name), "組織招待")
        self.assertEqual(str(OrganizationInvite._meta.verbose_name_plural), "組織招待")
