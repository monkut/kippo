from django.test import TestCase

from accounts.models import KippoOrganization, EmailDomain, KippoUser, OrganizationMembership
from projects.models import KippoProject, ProjectColumnSet
from tasks.models import KippoTask
from octocat.models import GithubAccessToken

from .admin import KippoAdminSite


DEFAULT_FIXTURES = [
    'required_bot_users',
    'default_columnset',
    'default_labelset',
]

DEFAULT_COLUMNSET_PK = '414e69c8-8ea3-4c9c-8129-6f5aac108fa2'


def setup_basic_project(organization=None):
    created_objects = {}
    user = KippoUser(
        username='octocat',
        github_login='octocat',
        password='test',
        email='a@github.com',
        is_staff=True,
    )
    user.save()
    created_objects['KippoUser'] = user
    if not organization:
        organization = KippoOrganization(
            name='github',
            github_organization_name='githubcodesorg',
            day_workhours=8,
            created_by=user,
            updated_by=user,
        )
        organization.save()
    created_objects['KippoOrganization'] = organization

    email_domain = EmailDomain(
        organization=organization,
        domain='github.com',
        is_staff_domain=True,
        created_by=user,
        updated_by=user,
    )
    email_domain.save()
    created_objects['EmailDomain'] = email_domain

    orgmembership = OrganizationMembership(
        user=user,
        organization=organization,
        is_developer=True,
        created_by=user,
        updated_by=user,
    )
    orgmembership.save()

    access_token = GithubAccessToken(
        organization=organization,
        token='kdakkfj',
        created_by=user,
        updated_by=user,
    )
    access_token.save()
    created_objects['GithubAccessToken'] = access_token

    default_columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
    kippo_project = KippoProject(
        organization=organization,
        name='octocat-test-project',
        github_project_url='https://github.com/orgs/githubcodesorg/projects/1',
        columnset=default_columnset,
        created_by=user,
        updated_by=user,
    )
    kippo_project.save()
    created_objects['KippoProject'] = kippo_project

    kippo_task = KippoTask(
        title='githubcodesorg test task',
        category='test category',
        project=kippo_project,
        assignee=user,
        created_by=user,
        updated_by=user,
        github_issue_html_url='https://github.com/repos/octocat/Hello-World/issues/1347',
        github_issue_api_url="https://api.github.com/repos/octocat/Hello-World/issues/1347",
    )
    kippo_task.save()
    created_objects['KippoTask'] = kippo_task

    return created_objects


class MockRequest:
    pass


class IsStaffModelAdminTestCaseBase(TestCase):
    fixtures = [
        'required_bot_users',
        'default_columnset',
        'default_labelset',
    ]

    def setUp(self):
        self.github_manager = KippoUser.objects.get(username='github-manager')
        self.organization = KippoOrganization.objects.create(
            name='test-organization',
            github_organization_name='isstaffmodeladmintestcasebase-testorg',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_organization = KippoOrganization.objects.create(
            name='other-test-organization',
            github_organization_name='isstaffmodeladmintestcasebase-other-testorg',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # create superuser and related request mock
        self.superuser_username = 'superuser_no_org'
        self.superuser_no_org = KippoUser.objects.create(
            username=self.superuser_username,
            is_superuser=True,
            is_staff=True,
        )
        self.super_user_request = MockRequest()
        self.super_user_request.user = self.superuser_no_org

        # create staff user and related request mock
        self.staffuser_username = 'staffuser_with_org'
        self.staffuser_with_org = KippoUser.objects.create(
            username=self.staffuser_username,
            is_superuser=False,
            is_staff=True,
        )
        # add membership
        membership = OrganizationMembership(
            user=self.staffuser_with_org,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        membership.save()

        self.staff_user_request = MockRequest()
        self.staff_user_request.user = self.staffuser_with_org

        self.site = KippoAdminSite()
