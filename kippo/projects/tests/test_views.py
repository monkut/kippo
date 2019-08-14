from http import HTTPStatus

from django.test import Client, TestCase
from django.conf import settings

from common.tests import DEFAULT_FIXTURES, setup_basic_project
from accounts.models import KippoUser, KippoOrganization, OrganizationMembership


class SetOrganizationTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created['KippoOrganization']
        self.user = created['KippoUser']
        self.github_manager = KippoUser.objects.get(username='github-manager')
        self.other_organization = KippoOrganization.objects.create(
            name='other-test-organization',
            github_organization_name='isstaffmodeladmintestcasebase-other-testorg',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # add membership
        membership = OrganizationMembership(
            user=self.user,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True
        )
        membership.save()
        self.nonmember_organization = KippoOrganization.objects.create(
            name='nonmember-test-organization',
            github_organization_name='isstaffmodeladmintestcasebase-nonmember-testorg',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.no_org_user = KippoUser(
            username='noorguser',
            github_login='noorguser',
            password='test',
            email='noorguser@github.com',
            is_staff=True,
        )
        self.no_org_user.save()

        self.client = Client()

    def test_set_organization__valid_user(self):
        url = f'{settings.URL_PREFIX}/projects/set/organization/{self.organization.id}/'
        self.client.force_login(self.user)
        response = self.client.get(url)
        expected = HTTPStatus.FOUND
        actual = response.status_code
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')
        self.assertTrue(self.client.session['organization_id'] == str(self.organization.id))

    def test_set_organization__valid_user_nonmember_org(self):
        url = f'{settings.URL_PREFIX}/projects/set/organization/{self.nonmember_organization.id}/'
        self.client.force_login(self.user)
        response = self.client.get(url)
        expected = HTTPStatus.FOUND
        actual = response.status_code
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

        actual = self.client.session['organization_id']
        self.assertTrue(actual != str(self.nonmember_organization.id))
        self.assertTrue(actual == str(self.user.organizations[0].id))

    def test_set_organization__user_no_org(self):
        url = f'{settings.URL_PREFIX}/projects/set/organization/{self.nonmember_organization.id}/'
        self.client.force_login(self.no_org_user)
        response = self.client.get(url)
        expected = HTTPStatus.BAD_REQUEST
        actual = response.status_code
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

        actual = self.client.session.get('organization_id', None)
        self.assertTrue(actual is None)
