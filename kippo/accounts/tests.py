from django.test import TestCase
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError, PermissionDenied
from .models import KippoOrganization, KippoUser, EmailDomain, OrganizationMembership


class KippoUserCreationTestCase(TestCase):
    fixtures = [
        'required_bot_users',
        'default_columnset',
        'default_labelset',
    ]

    def setUp(self):
        self.user = KippoUser(
            username='accounts-octocat',
            password='test',
            email='accounts@github.com',
            is_staff=True,
        )
        self.user.save()

        self.org = KippoOrganization(name='some org',
                                     github_organization_name='some-org',
                                     created_by=self.user,
                                     updated_by=self.user,
                                     )
        self.org.save()
        self.domain = 'kippo.org'
        self.emaildomain = EmailDomain(organization=self.org,
                                       domain=self.domain,
                                       is_staff_domain=True,
                                       created_by=self.user,
                                       updated_by=self.user,
                                       )
        self.emaildomain.save()

        self.nonstaff_org = KippoOrganization(
            name='nonstaff org',
            github_organization_name='nonstaff-org',
            created_by=self.user,
            updated_by=self.user,
        )
        self.nonstaff_org.save()
        self.nonstaff_org_domain = 'nonstaff.org'
        self.emaildomain = EmailDomain(organization=self.nonstaff_org,
                                       domain=self.nonstaff_org_domain,
                                       is_staff_domain=False,
                                       created_by=self.user,
                                       updated_by=self.user,
                                       )
        self.emaildomain.save()

    def test_create_kippouser(self):
        user1 = KippoUser(
            username='someuser',
            email='otheremail@other.com'
        )
        user1.save()

        user2 = KippoUser(
            username='otheruser',
            email=f'otheruser@{self.domain}'
        )
        user2.save()

        # add membership
        membership = OrganizationMembership(
            organization=self.org,
            is_developer=True,
            email=f'otheruser@{self.domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
        user2.memberships.add(membership)
        self.assertTrue(user2.is_staff)

    def test_invalid_emaildomain(self):
        invalid_email_domain = 'invalid'
        domain = EmailDomain(organization=self.org,
                             domain=invalid_email_domain,
                             created_by=self.user,
                             updated_by=self.user,
                             )
        with self.assertRaises(ValidationError):
            domain.clean()

    def test_valid_emaildomain(self):
        valid_email_domain = 'somedomain.com'
        domain = EmailDomain(organization=self.org,
                             domain=valid_email_domain,
                             created_by=self.user,
                             updated_by=self.user,
                             )
        domain.clean()
        self.assertTrue(domain)

    def test_valid_login_org_user(self):
        user = KippoUser(
            username='otheruser',
            is_active=False,
            email=f'otheruser@otherorgdomain.com',
        )
        password = 'testpassword'
        user.set_password(password)
        user.save()

        # add org membership
        membership = OrganizationMembership(
            organization=self.org,
            is_developer=True,
            email=f'otheruser@invaliddomain.com',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
        user.memberships.add(membership)
        authenticated_user = authenticate(
            username=user.username,
            password=password
        )
        self.assertTrue(authenticated_user)

    def test_valid_login_multi_org_user(self):
        user = KippoUser(
            username='otheruser',
            is_staff=False,
            is_active=False,
            email=f'otheruser@otherorgdomain.com',
        )
        password = 'testpassword'
        user.set_password(password)
        user.save()
        # add org membership
        membership = OrganizationMembership(
            organization=self.nonstaff_org,
            is_developer=True,
            email=f'otheruser@{self.nonstaff_org_domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
        user.memberships.add(membership)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_active)
        authenticated_user = authenticate(
            username=user.username,
            password=password
        )
        self.assertFalse(authenticated_user)

        # add org membership with is_staff_domain
        membership = OrganizationMembership(
            organization=self.org,
            is_developer=True,
            email=f'otheruser@invaliddomain.com',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
        user.memberships.add(membership)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_active)
        authenticated_user = authenticate(
            username=user.username,
            password=password
        )
        self.assertTrue(authenticated_user)

        self.assertTrue(user.memberships.count() == 2)

    def test_invalid_email_for_org_membership(self):
        user = KippoUser(
            username='otheruser',
            email=f'otheruser@invaliddomain.com',
        )
        user.save()
        # add membership
        membership = OrganizationMembership(
            organization=self.org,
            is_developer=True,
            email=f'otheruser@invaliddomain.com',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
        user.memberships.add(membership)
        self.assertTrue(user.is_staff)

        with self.assertRaises(ValidationError):
            membership.clean()

    def test_noemail_for_org_membership(self):
        """confirm that a user may belong to an org, but not have an email with that org"""
        user = KippoUser(
            username='otheruser',
            email=f'otheruser@otherorgdomain.com',
        )
        user.save()

        # add membership
        membership = OrganizationMembership(
            organization=self.org,
            is_developer=True,
            email=None,
            created_by=self.user,
            updated_by=self.user,
        )
        membership.clean()
        membership.save()
        user.memberships.add(membership)
        self.assertTrue(user.memberships.exists())

