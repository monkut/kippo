import datetime

from accounts.models import Country, KippoUser, OrganizationMembership, PersonalHoliday, PublicHoliday
from commons.definitions import SATURDAY, SUNDAY
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from django.utils import timezone
from tasks.models import KippoTask, KippoTaskStatus

from projects.models import KippoMilestone, KippoProject


class KippoProjectMethodsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.repository = created["GithubRepository"]
        self.task1 = created["KippoTask"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # default columnset done name
        self.planning_column_name = "planning"
        self.done_column_name = "done"

        # create task2
        self.task2 = KippoTask(
            title="task2",
            category="test category",
            project=self.project,
            assignee=self.user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f"https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2",
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2",
        )
        self.task2.save()

        # create task3
        self.task3 = KippoTask(
            title="task3",
            category="test category",
            project=self.project,
            assignee=self.user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f"https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/3",
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
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected}): {results}")

        actual_tasks = [s.task for s in results]
        self.assertTrue(self.task3 not in actual_tasks, f"done task({self.task3}) should not be returned but is: {results}")

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
        results, has_estimates = self.project.get_active_taskstatus(max_effort_date=max_effort_date)

        expected_activetask_count = 2
        self.assertEqual(len(results), expected_activetask_count)
        actual_tasks = [s.task for s in results]
        self.assertTrue(self.task3 not in actual_tasks, f"done task({self.task3}) should not be returned but is: {results}")

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
        for _ in range(10):
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

    def test_related_github_repositories(self):
        assert self.repository
        assert self.task1.project == self.project
        assert self.task1.github_issue_html_url
        assert self.repository.html_url
        expected = [self.repository]
        actual = list(self.project.related_github_repositories())
        self.assertEqual(actual, expected)


class KippoMilestoneMethodsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.repository = created["GithubRepository"]
        self.task1 = created["KippoTask"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        self.user2 = KippoUser(username="user2", github_login="user2", password="test", email="a@github.com", is_staff=True)  # noqa: S106
        self.user2.save()

        orgmembership = OrganizationMembership(
            user=self.user2,
            organization=self.organization,
            is_developer=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        orgmembership.save()

        # default columnset done name
        self.planning_column_name = "planning"
        self.done_column_name = "done"

        # prepare tasks
        self.task2 = KippoTask(
            title="task2",
            category="test category",
            project=self.project,
            assignee=self.user2,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f"https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2",
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2",
        )
        self.task2.save()

        # create task3
        self.task3 = KippoTask(
            title="task3",
            category="test category",
            project=self.project,
            assignee=self.user2,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f"https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/3",
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

        self.country = Country(name="Japan", alpha_2="jp", alpha_3="jpn", country_code="123", region="Asia")
        self.country.save()
        self.user.holiday_country = self.country
        self.user.save()

    def test_estimated_completion_date(self):
        assert KippoProject.objects.count() > 0
        # set start_date, target_date for project
        self.project.start_date = timezone.datetime(2020, 9, 1).date()
        self.project.target_date = timezone.datetime(2020, 11, 1).date()
        self.project.save()

        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        self.assertGreater(kippomilestone_1.estimated_completion_date, milestone1_startdate)

    def test_available_work_days(self):
        # defined in setUp() setup_basic_project()
        # includes:
        # - organization unassigned user
        # - created user
        # - created user2
        expected_mempership_count = 3
        assert OrganizationMembership.objects.count() == expected_mempership_count
        # remove user2 OrganizationMembership -- affects the available days calculation
        OrganizationMembership.objects.filter(user=self.user2).delete()

        # created OrganizationMembership defaults to 5 (mon-fri) days
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        actual = kippomilestone_1.available_work_days(start_date=timezone.datetime(2020, 9, 1).date())
        self.assertTrue(actual)

        # all days mon-fri between 9/1 to 9/20
        expected = sum(1 for day in range(1, 21, 1) if timezone.datetime(2020, 9, day).weekday() not in (SATURDAY, SUNDAY))
        self.assertEqual(actual, expected)

        # add personal holday and check again
        personalholiday = PersonalHoliday(
            user=self.user,
            day=timezone.datetime(2020, 9, 7).date(),  # monday
        )
        personalholiday.save()
        actual = kippomilestone_1.available_work_days(start_date=timezone.datetime(2020, 9, 1).date())
        self.assertTrue(actual)
        expected -= 1
        self.assertEqual(actual, expected)

        # add public holiday and check again
        public_holiday = PublicHoliday(
            country=self.country,
            name="test-public-holiday",
            day=timezone.datetime(2020, 9, 8).date(),  # tuesday
        )
        public_holiday.save()

        actual = kippomilestone_1.available_work_days(start_date=timezone.datetime(2020, 9, 1).date())
        self.assertTrue(actual)
        expected -= 1
        self.assertEqual(actual, expected)

    def test_estimated_work_days(self):
        expected_project_count = 0
        assert KippoProject.objects.count() > expected_project_count

        expected_taskstatus_count = 5
        assert KippoTaskStatus.objects.count() == expected_taskstatus_count, KippoTaskStatus.objects.count()
        # set start_date, target_date for project
        self.project.start_date = timezone.datetime(2020, 9, 1).date()
        self.project.target_date = timezone.datetime(2020, 11, 1).date()
        self.project.save()

        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()
        kippomilestone_1.skip_cache = True

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        actual = kippomilestone_1.estimated_work_days
        self.assertTrue(actual)

        task1_taskstatus = self.task1.latest_kippotaskstatus()
        expected = task1_taskstatus.estimate_days
        self.assertEqual(actual, expected)

        # add another task from another user
        self.task2.milestone = kippomilestone_1
        self.task2.save()
        task2_taskstatus = self.task2.latest_kippotaskstatus()

        self.task3.milestone = kippomilestone_1  # this task is done and SHOULD NOT be counted
        self.task3.save()

        expected = task2_taskstatus.estimate_days + task1_taskstatus.estimate_days
        actual = kippomilestone_1.estimated_work_days
        self.assertEqual(actual, expected)

    def test_tasks(self):
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        milestone_tasks = list(kippomilestone_1.tasks)

        expected = 1
        self.assertEqual(len(milestone_tasks), expected)

        milestone_task = milestone_tasks[0]
        self.assertEqual(milestone_task.id, self.task1.id)

    def test_active_tasks(self):
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        # assign task3 (done) to milestone
        self.task3.milestone = kippomilestone_1
        self.task3.save()

        all_tasks = list(kippomilestone_1.tasks)
        expected = 2
        self.assertEqual(len(all_tasks), expected)

        active_tasks = list(kippomilestone_1.active_tasks)

        expected = 1
        self.assertEqual(len(active_tasks), expected)

        milestone_task = active_tasks[0]
        self.assertEqual(milestone_task.id, self.task1.id)

        active_task_states = self.project.columnset.get_active_column_names()
        for task in active_tasks:
            status = task.latest_kippotaskstatus()
            self.assertIn(status.state, active_task_states)

    def test_delete_milestone(self):
        """Confirm that deleting the milestone does NOT delete attached tasks"""
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()
        task1_id = self.task1.id

        # delete milestone
        kippomilestone_1.delete()

        # confirm task still exists
        self.assertTrue(KippoTask.objects.filter(id=task1_id).exists())

    def test_get_assignee_task_counts(self):
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        self.task2.milestone = kippomilestone_1
        self.task2.save()

        # assign task3 (done) to milestone
        self.task3.milestone = kippomilestone_1
        self.task3.save()

        user1_active_tasks = 1
        user2_active_tasks = 1
        expected = user1_active_tasks + user2_active_tasks
        # returns "active" task counts
        actual = kippomilestone_1.get_assignee_task_counts()
        self.assertEqual(sum(actual.values()), expected)

        expected = 2
        self.assertEqual(len(actual.keys()), expected)

    def test_get_assignee_estimated_workdays(self):
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        self.task2.milestone = kippomilestone_1
        self.task2.save()

        # assign task3 (done) to milestone
        self.task3.milestone = kippomilestone_1
        self.task3.save()

        expected_user1_estimated_workdays = 3
        expected_user2_estimated_workdays = 3
        expected = expected_user1_estimated_workdays + expected_user2_estimated_workdays
        actual = kippomilestone_1.get_assignee_estimated_workdays()
        self.assertEqual(sum(actual.values()), expected, actual)

        expected_assignee_count = 2
        self.assertEqual(len(actual), expected_assignee_count)
        self.assertIn(self.task1.assignee, actual)
        self.assertIn(self.task2.assignee, actual)
