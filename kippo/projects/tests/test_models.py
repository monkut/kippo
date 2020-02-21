import datetime

from django.test import Client, TestCase
from django.utils import timezone

from common.tests import DEFAULT_FIXTURES, setup_basic_project
from accounts.models import KippoUser
from tasks.models import KippoTask, KippoTaskStatus


class KippoProjectMethodsTestCase(TestCase):
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
        self.planning_column_name = 'planning'
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
            state=self.planning_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status1.save()

        self.task1_seconddate = timezone.datetime(2019, 8, 17).date()
        self.task1_status2 = KippoTaskStatus(
            task=self.task1,
            state=self.planning_column_name,
            effort_date=self.task1_seconddate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status2.save()

        self.task2_status1 = KippoTaskStatus(
            task=self.task2,
            state=self.planning_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task2_status1.save()

        self.task2_seconddate = timezone.datetime(2019, 8, 19).date()
        self.task2_status2 = KippoTaskStatus(
            task=self.task2,
            state=self.planning_column_name,
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

    def test_get_active_taskstatus__no_max_date(self):
        results, has_estimates = self.project.get_active_taskstatus()

        expected = 2
        actual = len(results)
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected}): {results}')

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
        max_effort_date = timezone.datetime(2019, 8, 15).date()
        results, has_estimates = self.project.get_active_taskstatus(
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

    def test__get_active_taskstatus__done__latest_taskstatus(self):
        new_date = timezone.datetime(2019, 12, 19)
        for i in range(10):
            task2_status = KippoTaskStatus(
                task=self.task2,
                state=self.done_column_name,
                effort_date=new_date.date(),
                estimate_days=3,
                created_by=self.github_manager,
                updated_by=self.github_manager,
            )
            task2_status.save()
            new_date += datetime.timedelta(days=1)

        # make sure that task2 is not returned now that it is 'done'
        results, has_estimates = self.project.get_active_taskstatus()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], self.task1_status2)
