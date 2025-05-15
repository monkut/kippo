from commons.tests import IsStaffModelAdminTestCaseBase
from django.conf import settings
from django.utils import timezone

from accounts.functions import process_organizationinvites
from accounts.models import KippoUser, OrganizationInvite, OrganizationMembership


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

    def test_process_for_organizationinvites__expired(self):
        user_name = "new_user"
        user_email = f"new@{self.organization_domain}"
        user = KippoUser.objects.create(username=user_name, email=user_email, is_superuser=False, is_staff=False)
        expired_days = settings.ORGANIZATIONINVITE_EXPIRATION_DAYS + 1
        expired_date = timezone.now() + timezone.timedelta(days=-expired_days)
        invite = OrganizationInvite(
            email=user_email,
            expiration_date=expired_date,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        invite.save()

        assert OrganizationMembership.objects.filter(user=user).count() == 0
        process_organizationinvites(None, user, None, None, None)
        membership = OrganizationMembership.objects.filter(user=user).first()
        self.assertFalse(membership)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

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
