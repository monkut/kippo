from commons.tests import IsStaffModelAdminTestCaseBase
from django.utils import timezone

from accounts.exceptions import OrganizationInviteExpiredError
from accounts.functions import process_organizationinvites
from accounts.models import KippoOrganization, KippoUser, OrganizationInvite, OrganizationMembership


class ProcessOrganizationInvitesTestCase(IsStaffModelAdminTestCaseBase):
    """Test the check_for_organization_invites function."""

    def test_process_organizationinvites__new(self):
        """Confirm a valid new user is processed correctly"""
        user_name = "new_user"
        user_email = f"new@{self.organization_domain}"
        user = KippoUser.objects.create(username=user_name, email=user_email, is_superuser=False, is_staff=False)
        invite = OrganizationInvite.objects.create(
            email=user_email,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        invite.save()

        assert OrganizationMembership.objects.filter(user=user).count() == 0
        process_organizationinvites(None, user, None, None, None)
        membership = OrganizationMembership.objects.filter(user=user).first()
        self.assertTrue(membership)
        self.assertEqual(membership.organization, self.organization)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)

        # check invite was udpated
        invite.refresh_from_db()
        today = timezone.localdate()
        self.assertGreater(invite.expiration_date, today)
        self.assertTrue(invite.is_complete)
        self.assertTrue(invite.processed_datetime)

    def test_process_organizationinvites__none(self):
        """Confirm that a user with no invites is not processed"""
        user_name = "new_user"
        user_email = f"new@{self.organization_domain}"
        user = KippoUser.objects.create(username=user_name, email=user_email, is_superuser=False, is_staff=False)
        process_organizationinvites(None, user, None, None, None)
        membership = OrganizationMembership.objects.filter(user=user).first()
        self.assertFalse(membership)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def _create_expired_invite(self, user_email: str, organization: KippoOrganization | None = None, days_expired: int = 1) -> OrganizationInvite:
        expired_date = (timezone.now() - timezone.timedelta(days=days_expired)).date()
        invite = OrganizationInvite(
            email=user_email,
            expiration_date=expired_date,
            organization=organization or self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        invite.save()
        return invite

    def test_process_for_organizationinvites__expired(self):
        """An expired invite for a user without any organization denies the login with an explanatory error"""
        user_name = "new_user"
        user_email = f"new@{self.organization_domain}"
        user = KippoUser.objects.create(username=user_name, email=user_email, is_superuser=False, is_staff=False)
        invite = self._create_expired_invite(user_email)

        assert OrganizationMembership.objects.filter(user=user).count() == 0
        with self.assertRaises(OrganizationInviteExpiredError) as context:
            process_organizationinvites(None, user, None, None, None)
        message = str(context.exception)
        self.assertIn(self.organization.name, message)
        self.assertIn(invite.expiration_date.strftime("%Y-%m-%d"), message)
        self.assertIn("expired", message)
        self.assertIn("new invitation", message)

        membership = OrganizationMembership.objects.filter(user=user).first()
        self.assertFalse(membership)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        invite.refresh_from_db()
        self.assertFalse(invite.is_complete)

    def test_process_for_organizationinvites__expired_names_latest_expired_invite(self):
        user_email = f"new@{self.organization_domain}"
        user = KippoUser.objects.create(username="new_user", email=user_email, is_superuser=False, is_staff=False)
        self._create_expired_invite(user_email, organization=self.other_organization, days_expired=10)
        latest = self._create_expired_invite(user_email, organization=self.organization, days_expired=2)

        with self.assertRaises(OrganizationInviteExpiredError) as context:
            process_organizationinvites(None, user, None, None, None)
        self.assertIn(latest.organization.name, str(context.exception))
        self.assertNotIn(self.other_organization.name, str(context.exception))

    def test_process_for_organizationinvites__expired_ignored_for_existing_member(self):
        """An expired invite must not block the login of a user who already belongs to an organization"""
        user_email = f"member@{self.organization_domain}"
        user = KippoUser.objects.create(username="existing_member", email=user_email, is_superuser=False, is_staff=True)
        OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self._create_expired_invite(user_email, organization=self.other_organization)

        process_organizationinvites(None, user, None, None, None)  # must not raise
        self.assertEqual(OrganizationMembership.objects.filter(user=user).count(), 1)

    def test_process_for_organizationinvites__valid_invite_alongside_expired(self):
        """A valid invite is processed and an older expired invite does not block the login"""
        user_email = f"new@{self.organization_domain}"
        user = KippoUser.objects.create(username="new_user", email=user_email, is_superuser=False, is_staff=False)
        expired_invite = self._create_expired_invite(user_email)
        valid_invite = OrganizationInvite.objects.create(
            email=user_email,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        process_organizationinvites(None, user, None, None, None)
        self.assertEqual(OrganizationMembership.objects.filter(user=user, organization=self.organization).count(), 1)
        valid_invite.refresh_from_db()
        self.assertTrue(valid_invite.is_complete)
        expired_invite.refresh_from_db()
        self.assertFalse(expired_invite.is_complete)

    def test_process_for_organizationinvites__refreshed_invite_after_expired_login_attempt(self):
        """A user created on an earlier (denied) login attempt is processed once the invite is re-issued"""
        user_email = f"new@{self.organization_domain}"
        user = KippoUser.objects.create(username="new_user", email=user_email, is_superuser=False, is_staff=False)
        user.date_joined = timezone.now() - timezone.timedelta(days=3)
        user.save()
        self._create_expired_invite(user_email)
        with self.assertRaises(OrganizationInviteExpiredError):
            process_organizationinvites(None, user, None, None, None)

        # organization admin re-issues the invite
        OrganizationInvite.objects.create(
            email=user_email,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        process_organizationinvites(None, user, None, None, None)
        self.assertEqual(OrganizationMembership.objects.filter(user=user, organization=self.organization).count(), 1)

    def test_process_for_organizationinvites__expired_ignored_for_superuser(self):
        user_email = f"root@{self.organization_domain}"
        user = KippoUser.objects.create(username="root_user", email=user_email, is_superuser=True, is_staff=True)
        self._create_expired_invite(user_email)
        process_organizationinvites(None, user, None, None, None)  # must not raise

    def test_process_for_organizationinvites__valid_invite_for_existing_membership(self):
        """A valid invite to an organization the user already belongs to completes without a duplicate membership"""
        user_email = f"member@{self.organization_domain}"
        user = KippoUser.objects.create(username="existing_member", email=user_email, is_superuser=False, is_staff=True)
        OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        invite = OrganizationInvite.objects.create(
            email=user_email,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        process_organizationinvites(None, user, None, None, None)  # must not raise IntegrityError
        self.assertEqual(OrganizationMembership.objects.filter(user=user, organization=self.organization).count(), 1)
        invite.refresh_from_db()
        self.assertTrue(invite.is_complete)

    def test_process_for_organizationinvites__no_email(self):
        user = KippoUser.objects.create(username="no_email_user", email="", is_superuser=False, is_staff=False)
        process_organizationinvites(None, user, None, None, None)  # must not raise
        self.assertFalse(OrganizationMembership.objects.filter(user=user).exists())

    def test_process_for_organizationinvites__processed(self):
        """Test the check_for_organization_invites function."""
        user_name = "new_user"
        user_email = f"new@{self.organization_domain}"
        user = KippoUser.objects.create(username=user_name, email=user_email, is_superuser=False, is_staff=False)
        invite = OrganizationInvite.objects.create(
            email=user_email,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        invite.save()

        assert OrganizationMembership.objects.filter(user=user).count() == 0
        process_organizationinvites(None, user, None, None, None)
        membership = OrganizationMembership.objects.filter(user=user).first()
        self.assertTrue(membership)
        self.assertEqual(membership.organization, self.organization)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)

        # check invite was updated
        invite.refresh_from_db()
        today = timezone.localdate()
        self.assertGreater(invite.expiration_date, today)
        self.assertTrue(invite.is_complete)
        self.assertTrue(invite.processed_datetime)
        processed_datetime = invite.processed_datetime

        # try again, make sure no changes are made
        process_organizationinvites(None, user, None, None, None)

        expected_orgmemberships = 1
        self.assertEqual(OrganizationMembership.objects.filter(user=user).count(), expected_orgmemberships)
        invite.refresh_from_db()
        self.assertEqual(invite.processed_datetime, processed_datetime)
        self.assertTrue(invite.is_complete)
