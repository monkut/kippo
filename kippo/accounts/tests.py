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
            user=user2,
            organization=self.org,
            is_developer=True,
            email=f'otheruser@{self.domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
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
            user=user,
            organization=self.org,
            is_developer=True,
            email=f'otheruser@invaliddomain.com',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
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
            user=user,
            organization=self.nonstaff_org,
            is_developer=True,
            email=f'otheruser@{self.nonstaff_org_domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()

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
            user=user,
            organization=self.org,
            is_developer=True,
            email=f'otheruser@invaliddomain.com',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
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
            user=user,
            organization=self.org,
            is_developer=True,
            email=f'otheruser@invaliddomain.com',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
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
            user=user,
            organization=self.org,
            is_developer=True,
            email=None,
            created_by=self.user,
            updated_by=self.user,
        )
        membership.clean()
        membership.save()
        self.assertTrue(user.memberships.exists())

    def test_organization_get_github_developer_kippousers(self):
        user = KippoUser(
            username='otheruser',
            github_login='otheruser-gh',
            is_staff=False,
            is_active=False,
            email=f'otheruser@otherorgdomain.com',
        )
        password = 'testpassword'
        user.set_password(password)
        user.save()

        another_user = KippoUser(
            username='anotheruser',
            github_login='anotheruser-gh',
            is_staff=False,
            is_active=False,
            email=f'anotheruser@otherorgdomain.com',
        )
        another_user.save()

        third_user = KippoUser(
            username='thirduser',
            github_login='thirduser-gh',
            is_staff=False,
            is_active=False,
            email=f'thirduser@otherorgdomain.com',
        )
        third_user.save()

        fourth_user = KippoUser(
            username='fourth_user',
            is_staff=False,
            is_active=False,
            email=f'fourth_user@otherorgdomain.com',
        )
        fourth_user.save()

        # add org membership
        membership = OrganizationMembership(
            user=user,
            organization=self.nonstaff_org,
            is_developer=True,
            email=f'otheruser@{self.nonstaff_org_domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
        user.refresh_from_db()

        # add org membership with is_staff_domain
        membership = OrganizationMembership(
            user=user,
            organization=self.org,
            is_developer=True,
            email=f'otheruser@{self.domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()
        user.refresh_from_db()

        # add org membership with is_staff_domain
        membership = OrganizationMembership(
            user=another_user,
            organization=self.org,
            is_developer=True,
            email=f'anotheruser@{self.domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()

        # add org membership with is_staff_domain
        membership = OrganizationMembership(
            user=third_user,
            organization=self.org,
            is_developer=False,
            email=f'thirduser@{self.domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()

        # add org membership with is_staff_domain
        membership = OrganizationMembership(
            user=fourth_user,
            organization=self.org,
            is_developer=True,
            email=f'fourth_user@{self.domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        membership.save()

        users = self.org.get_github_developer_kippousers()
        self.assertTrue(len(users) == 3)  # users created in setUp + auto-created 'unassigned' user

        expected_usernames = (
            'otheruser',
            'anotheruser',
            f'github-unassigned-{self.org.name}'
        )
        actual_usernames = []
        for u in users:
            actual_usernames.append(u.username)
        self.assertTrue(set(expected_usernames) == set(actual_usernames), f'expected({set(expected_usernames)}) != actual({set(actual_usernames)})')

    def test_organizationmembership_get_workday_identifers(self):
        user = KippoUser(
            username='otheruser',
            github_login='otheruser-gh',
            is_staff=False,
            is_active=False,
            email=f'otheruser@otherorgdomain.com',
        )
        password = 'testpassword'
        user.set_password(password)
        user.save()

        another_user = KippoUser(
            username='anotheruser',
            github_login='anotheruser-gh',
            is_staff=False,
            is_active=False,
            email=f'anotheruser@otherorgdomain.com',
        )
        another_user.save()

        third_user = KippoUser(
            username='thirduser',
            github_login='thirduser-gh',
            is_staff=False,
            is_active=False,
            email=f'thirduser@otherorgdomain.com',
        )
        third_user.save()

        fourth_user = KippoUser(
            username='fourth_user',
            is_staff=False,
            is_active=False,
            email=f'fourth_user@otherorgdomain.com',
        )
        fourth_user.save()

        # add org membership
        user_membership1 = OrganizationMembership(
            user=user,
            organization=self.nonstaff_org,
            is_developer=True,
            email=f'otheruser@{self.nonstaff_org_domain}',
            created_by=self.user,
            updated_by=self.user,
        )
        user_membership1.save()
        user.refresh_from_db()

        expected_workdays = (
            'Mon',
            'Tue',
            'Wed',
            'Thu',
            'Fri'
        )
        actual_workdays = user_membership1.get_workday_identifers()
        self.assertTrue(actual_workdays == expected_workdays, f'actual({actual_workdays}) != expected({expected_workdays})')

        # add org membership with is_staff_domain
        user_membership2 = OrganizationMembership(
            user=user,
            organization=self.org,
            is_developer=True,
            email=f'otheruser@{self.domain}',
            sunday=True,
            created_by=self.user,
            updated_by=self.user,
        )
        user_membership2.save()
        user.refresh_from_db()
        expected_workdays = (
            'Sun',
            'Mon',
            'Tue',
            'Wed',
            'Thu',
            'Fri'
        )
        actual_workdays = user_membership2.get_workday_identifers()
        self.assertTrue(actual_workdays == expected_workdays, f'actual({actual_workdays}) != expected({expected_workdays})')

        # add org membership with is_staff_domain
        another_membership = OrganizationMembership(
            user=another_user,
            organization=self.org,
            is_developer=True,
            email=f'anotheruser@{self.domain}',
            monday=False,
            created_by=self.user,
            updated_by=self.user,
        )
        another_membership.save()
        expected_workdays = (
            'Tue',
            'Wed',
            'Thu',
            'Fri'
        )
        actual_workdays = another_membership.get_workday_identifers()
        self.assertTrue(actual_workdays == expected_workdays, f'actual({actual_workdays}) != expected({expected_workdays})')

        # add org membership with is_staff_domain
        third_membership = OrganizationMembership(
            user=third_user,
            organization=self.org,
            is_developer=False,
            email=f'thirduser@{self.domain}',
            monday=False,
            tuesday=False,
            saturday=True,
            created_by=self.user,
            updated_by=self.user,
        )
        third_membership.save()
        expected_workdays = (
            'Wed',
            'Thu',
            'Fri',
            'Sat'
        )
        actual_workdays = third_membership.get_workday_identifers()
        self.assertTrue(actual_workdays == expected_workdays, f'actual({actual_workdays}) != expected({expected_workdays})')
