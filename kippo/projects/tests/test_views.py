from http import HTTPStatus

from django.test import Client, TestCase
from django.conf import settings
from django.utils import timezone

from common.tests import DEFAULT_FIXTURES, setup_basic_project
from accounts.models import KippoUser, KippoOrganization, OrganizationMembership
from tasks.models import KippoTask, KippoTaskStatus
from ..views import _get_active_taskstatus_from_projects


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


class ViewsHelperFunctionsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created['KippoOrganization']
        self.user = created['KippoUser']
        self.project = created['KippoProject']
        self.repository = created['GithubRepository']
        self.task1 = created['KippoTask']
        self.github_manager = KippoUser.objects.get(username='github-manager')

        # default columnset done name
        self.done_column_name = 'done'

        # create task2
        self.task2 = KippoTask(
            title='task2',
            category='test category',
            project=self.project,
            assignee=self.user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f'https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2',
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2",
        )
        self.task2.save()

        # create task3
        self.task3 = KippoTask(
            title='task3',
            category='test category',
            project=self.project,
            assignee=self.user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f'https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/3',
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/3",
        )
        self.task3.save()

        self.firstdate = timezone.datetime(2019, 8, 14).date()
        # create KippoTaskStatus objects
        # create existing taskstatus
        self.task1_status1 = KippoTaskStatus(
            task=self.task1,
            state='open',
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status1.save()

        self.task1_seconddate = timezone.datetime(2019, 8, 17).date()
        self.task1_status2 = KippoTaskStatus(
            task=self.task1,
            state='open',
            effort_date=self.task1_seconddate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status2.save()

        self.task2_status1 = KippoTaskStatus(
            task=self.task2,
            state='open',
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task2_status1.save()

        self.task2_seconddate = timezone.datetime(2019, 8, 19).date()
        self.task2_status2 = KippoTaskStatus(
            task=self.task2,
            state='open',
            effort_date=self.task2_seconddate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task2_status2.save()

        # task3 taskstatus
        self.task3_status1 = KippoTaskStatus(
            task=self.task3,
            state=self.done_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task3_status1.save()

    def test__get_active_taskstatus_from_projects__without_max_effort_date(self):
        projects = [self.project]
        results, has_estimates = _get_active_taskstatus_from_projects(
            projects=projects,
        )
        self.assertTrue(len(results) == 2)

        actual_tasks = [s.task for s in results]
        self.assertTrue(
            self.task3 not in actual_tasks,
            f'done task({self.task3}) should not be returned but is: {results}'
        )

        expected_tasks = [self.task1, self.task2]
        self.assertTrue(all(t in expected_tasks for t in actual_tasks))
        self.assertTrue(all(t in actual_tasks for t in expected_tasks))

        task1_tested = False
        task2_tested = False
        for taskstatus in results:
            if taskstatus.task == self.task1:
                self.assertTrue(taskstatus.effort_date == self.task1_seconddate)
                task1_tested = True
            elif taskstatus.task == self.task2:
                self.assertTrue(taskstatus.effort_date == self.task2_seconddate)
                task2_tested = True
        self.assertTrue(all([task1_tested, task2_tested]))

    def test__get_active_taskstatus_from_projects__with_max_effort_date(self):
        projects = [self.project]
        max_effort_date = timezone.datetime(2019, 8, 15).date()
        results, has_estimates = _get_active_taskstatus_from_projects(
            projects=projects,
            max_effort_date=max_effort_date
        )
        self.assertTrue(len(results) == 2)
        actual_tasks = [s.task for s in results]
        self.assertTrue(
            self.task3 not in actual_tasks,
            f'done task({self.task3}) should not be returned but is: {results}'
        )

        expected_tasks = [self.task1, self.task2]
        self.assertTrue(all(t in expected_tasks for t in actual_tasks))
        self.assertTrue(all(t in actual_tasks for t in expected_tasks))

        task1_tested = False
        task2_tested = False
        for taskstatus in results:
            if taskstatus.task == self.task1:
                self.assertTrue(taskstatus.effort_date == self.firstdate)
                task1_tested = True
            elif taskstatus.task == self.task2:
                self.assertTrue(taskstatus.effort_date == self.firstdate)
                task2_tested = True
        self.assertTrue(all([task1_tested, task2_tested]))
