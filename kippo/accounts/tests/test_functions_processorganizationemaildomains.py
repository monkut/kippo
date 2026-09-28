from commons.tests import IsStaffModelAdminTestCaseBase
from django.utils import timezone

from accounts.functions import process_organization_email_domains, process_organizationinvites
from accounts.models import EmailDomain, KippoUser, OrganizationMembership


class ProcessOrganizationEmailDomainsTestCase(IsStaffModelAdminTestCaseBase):
    def test_new_user_with_staff_domain_is_added(self):
        """A new user whose email matches an organization's staff domain becomes a staff member on first login"""
        user = KippoUser.objects.create(username="new_user", email=f"new@{self.organization_domain}", is_superuser=False, is_staff=False)

        process_organization_email_domains(None, user, None)
        self.assertTrue(user.is_staff)  # the pipeline's user instance logs in with access
        membership = OrganizationMembership.objects.get(user=user)
        self.assertEqual(membership.organization, self.organization)
        self.assertEqual(membership.email, user.email)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_active)

    def test_existing_user_row_is_added(self):
        """A user row left from an earlier login (older than the first request) is still added"""
        user = KippoUser.objects.create(username="old_user", email=f"old@{self.organization_domain}", is_superuser=False, is_staff=False)
        user.date_joined = timezone.now() - timezone.timedelta(days=3)
        user.save()

        process_organization_email_domains(None, user, None)
        self.assertTrue(OrganizationMembership.objects.filter(user=user, organization=self.organization).exists())
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_domain_match_ignores_case(self):
        EmailDomain.objects.filter(organization=self.organization).update(domain=self.organization_domain.upper())
        user = KippoUser.objects.create(username="new_user", email=f"new@{self.organization_domain}", is_superuser=False, is_staff=False)

        process_organization_email_domains(None, user, None)
        self.assertTrue(OrganizationMembership.objects.filter(user=user, organization=self.organization).exists())

    def test_non_staff_domain_is_not_added(self):
        EmailDomain.objects.filter(organization=self.organization).update(is_staff_domain=False)
        user = KippoUser.objects.create(username="new_user", email=f"new@{self.organization_domain}", is_superuser=False, is_staff=False)

        process_organization_email_domains(None, user, None)
        self.assertFalse(OrganizationMembership.objects.filter(user=user).exists())
        self.assertFalse(user.is_staff)

    def test_unknown_domain_is_not_added(self):
        user = KippoUser.objects.create(username="new_user", email="new@unknown-example.com", is_superuser=False, is_staff=False)

        process_organization_email_domains(None, user, None)
        self.assertFalse(OrganizationMembership.objects.filter(user=user).exists())
        self.assertFalse(user.is_staff)

    def test_inactive_user_is_not_added(self):
        """is_active=False is how an administrator revokes a domain user, so login must not re-activate them"""
        user = KippoUser.objects.create(
            username="revoked_user", email=f"revoked@{self.organization_domain}", is_superuser=False, is_staff=False, is_active=False
        )

        process_organization_email_domains(None, user, None)
        self.assertFalse(OrganizationMembership.objects.filter(user=user).exists())
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_staff_user_skips_lookup(self):
        user = KippoUser.objects.create(username="staff_user", email=f"staff@{self.organization_domain}", is_superuser=False, is_staff=True)
        with self.assertNumQueries(0):
            process_organization_email_domains(None, user, None)

    def test_second_login_after_domain_add_runs_no_queries(self):
        """Once the domain step has made the user staff, later logins run no onboarding queries"""
        user = KippoUser.objects.create(username="new_user", email=f"new@{self.organization_domain}", is_superuser=False, is_staff=False)
        process_organization_email_domains(None, user, None)
        process_organizationinvites(None, user, None)

        user = KippoUser.objects.get(pk=user.pk)  # the next login loads the user from the DB
        with self.assertNumQueries(0):
            process_organization_email_domains(None, user, None)
            process_organizationinvites(None, user, None)

    def test_expired_invite_does_not_deny_domain_user(self):
        """The domain step runs first, so an expired invite no longer denies a same-domain user"""
        user_email = f"new@{self.organization_domain}"
        user = KippoUser.objects.create(username="new_user", email=user_email, is_superuser=False, is_staff=False)
        self._create_expired_invite(user_email)

        process_organization_email_domains(None, user, None)
        process_organizationinvites(None, user, None)  # must not raise: the user is now staff
        self.assertTrue(OrganizationMembership.objects.filter(user=user, organization=self.organization).exists())

    def _create_expired_invite(self, user_email: str) -> None:
        from accounts.models import OrganizationInvite

        OrganizationInvite(
            email=user_email,
            expiration_date=(timezone.now() - timezone.timedelta(days=1)).date(),
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        ).save()
