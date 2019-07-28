from django.test import TestCase
from django.utils import timezone

from tasks.periodic.tasks import OrganizationIssueProcessor, get_existing_kippo_project, collect_github_project_issues
from projects.models import KippoProject, ActiveKippoProject, ProjectColumnSet
from accounts.models import KippoUser, KippoOrganization, OrganizationMembership
from octocat.models import GithubAccessToken
from common.tests import DEFAULT_COLUMNSET_PK


DEFAULT_GITHUB_PROJECT_URL = 'https://github.com/ghdummyorg/reponame/'


class GithubOrganizationProjectMock:
    def __init__(self, html_url=DEFAULT_GITHUB_PROJECT_URL):
        self.html_url = html_url


class PeriodicTaskFunctionsTestCase(TestCase):
    fixtures = [
        'default_columnset',
        'required_bot_users',
    ]

    def setUp(self):
        self.target_github_project_url = DEFAULT_GITHUB_PROJECT_URL
        self.other_github_project_url = 'https://github.com/other/repo/'
        now = timezone.now()
        start_date = (now - timezone.timedelta(days=7)).date()
        end_date = (now + timezone.timedelta(days=7)).date()

        github_manager_user = KippoUser.objects.get(username='github-manager')
        dummy_organization = KippoOrganization(
            name='dummy-org',
            github_organization_name='ghdummyorg',
            created_by=github_manager_user,
            updated_by=github_manager_user,
        )
        dummy_organization.save()

        default_columnset = ProjectColumnSet.objects.get(id=DEFAULT_COLUMNSET_PK)

        # create closed project
        self.closed_project = KippoProject(
            organization=dummy_organization,
            name='closed-project-A',
            created_by=github_manager_user,
            updated_by=github_manager_user,
            is_closed=True,
            github_project_url=self.target_github_project_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset
        )
        self.closed_project.save()

        # create opened project
        self.opened_project = KippoProject(
            organization=dummy_organization,
            name='opened-project-A',
            created_by=github_manager_user,
            updated_by=github_manager_user,
            is_closed=False,
            github_project_url=self.target_github_project_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset
        )
        self.opened_project.save()

        # create opened un-linked project
        self.other_opened_project = KippoProject(
            organization=dummy_organization,
            name='other-opened-project-A',
            created_by=github_manager_user,
            updated_by=github_manager_user,
            is_closed=False,
            github_project_url=self.other_github_project_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset
        )

    def test_get_existing_kippo_project(self):
        github_project = GithubOrganizationProjectMock(html_url=DEFAULT_GITHUB_PROJECT_URL)

        existing_open_projects = list(ActiveKippoProject.objects.all())
        related_kippo_project = get_existing_kippo_project(github_project, existing_open_projects)
        self.assertTrue(related_kippo_project.pk == self.opened_project.pk)


class OrganizationIssueProcessorTestCase(TestCase):
    fixtures = [
        'default_columnset',
        'required_bot_users',
    ]

    def setUp(self):
        self.target_github_project_url = DEFAULT_GITHUB_PROJECT_URL
        self.other_github_project_url = 'https://github.com/other/repo/'
        now = timezone.now()
        start_date = (now - timezone.timedelta(days=7)).date()
        end_date = (now + timezone.timedelta(days=7)).date()

        github_manager_user = KippoUser.objects.get(username='github-manager')
        self.organization = KippoOrganization(
            name='dummy-org',
            github_organization_name='ghdummyorg',
            created_by=github_manager_user,
            updated_by=github_manager_user,
        )
        self.organization.save()

        token = GithubAccessToken(
            organization=self.organization,
            token='abcdefABCDEF1234567890',
            created_by=github_manager_user,
            updated_by=github_manager_user,
        )
        token.save()

        default_columnset = ProjectColumnSet.objects.get(id=DEFAULT_COLUMNSET_PK)

        # create closed project
        self.closed_project = KippoProject(
            organization=self.organization,
            name='closed-project-A',
            created_by=github_manager_user,
            updated_by=github_manager_user,
            is_closed=True,
            github_project_url=self.target_github_project_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset
        )
        self.closed_project.save()

        # create opened project
        self.opened_project = KippoProject(
            organization=self.organization,
            name='opened-project-A',
            created_by=github_manager_user,
            updated_by=github_manager_user,
            is_closed=False,
            github_project_url=self.target_github_project_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset
        )
        self.opened_project.save()

        # create opened un-linked project
        self.other_opened_project = KippoProject(
            organization=self.organization,
            name='other-opened-project-A',
            created_by=github_manager_user,
            updated_by=github_manager_user,
            is_closed=False,
            github_project_url=self.other_github_project_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset
        )

    def test_organizationissueprocessor___init__(self):
        issue_processor = OrganizationIssueProcessor(
            organization=self.organization,
            status_effort_date=timezone.datetime(2019, 6, 5).date(),
            github_project_urls=[self.target_github_project_url]
        )
        self.assertTrue(issue_processor)
