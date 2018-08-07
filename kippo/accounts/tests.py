from django.test import TestCase
from django.core.exceptions import ValidationError
from .models import KippoOrganization, KippoUser, EmailDomain


class KippoUserCreationTestCase(TestCase):

    def setUp(self):
        self.org = KippoOrganization(name='some org',
                                     github_organization_name='some-org')
        self.org.save()
        self.domain = 'kippo.org'
        self.emaildomain = EmailDomain(organization=self.org,
                                       domain=self.domain,
                                       is_staff_domain=True)

    def test_create_kippouser(self):
        user1 = KippoUser(organization=self.org,
                         username='someuser',
                         email='otheremail@other.com')
        user1.save()
        self.assertFalse(user1.is_staff)

        user2 = KippoUser(organization=self.org,
                          username='otheruser',
                          email=f'otheruser@{self.domain}')
        user2.save()
        self.assertTrue(user2.is_staff)

    def test_invalid_emaildomain(self):
        invalid_email_domain = 'invalid'
        domain = EmailDomain(organization=self.org,
                             domain=invalid_email_domain)
        with self.assertRaises(ValidationError):
            domain.clean()

    def test_valid_emaildomain(self):
        valid_email_domain = 'somedomain.com'
        domain = EmailDomain(organization=self.org,
                             domain=valid_email_domain)
        domain.clean()
        self.assertTrue(domain)




