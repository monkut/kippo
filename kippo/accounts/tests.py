from django.test import TestCase
from django.core.exceptions import ValidationError, PermissionDenied
from .models import KippoOrganization, KippoUser, EmailDomain


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
            is_developer=True,
        )
        self.user.save(ignore_email_domain_check=True)

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

    def test_create_kippouser(self):
        user1 = KippoUser(organization=self.org,
                         username='someuser',
                         email='otheremail@other.com')
        with self.assertRaises(PermissionDenied):
            user1.save()

        user2 = KippoUser(organization=self.org,
                          username='otheruser',
                          email=f'otheruser@{self.domain}')
        user2.save()
        self.assertTrue(user2.is_staff)

    def test_invalid_emaildomain(self):
        user = KippoUser(
            username='octocat',
            password='test',
            email='a@github.com',
            is_staff=True,
            is_developer=True,
        )
        user.save(ignore_email_domain_check=True)
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




