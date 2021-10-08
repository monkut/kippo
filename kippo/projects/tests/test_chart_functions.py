from accounts.models import EmailDomain, KippoOrganization, KippoUser, OrganizationMembership
from common.tests import DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES
from django.test import TestCase
from django.utils import timezone
from tasks.models import KippoTask, KippoTaskStatus

from ..charts.functions import get_project_weekly_effort, prepare_project_plot_data
from ..models import KippoMilestone, KippoProject, ProjectColumnSet


class ProjectsChartFunctionsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.cli_manager = KippoUser.objects.get(username="cli-manager")

        self.organization = KippoOrganization(
            name="some org", github_organization_name="some-org", created_by=self.cli_manager, updated_by=self.cli_manager
        )
        self.organization.save()
        self.domain = "kippo.org"
        self.emaildomain = EmailDomain(
            organization=self.organization, domain=self.domain, is_staff_domain=True, created_by=self.cli_manager, updated_by=self.cli_manager
        )
        self.emaildomain.save()

        self.user1 = KippoUser(username="user1", github_login="user1", password="test", email="user1@github.com", is_staff=True)
        self.user1.save()
        self.user1_membership = OrganizationMembership(
            user=self.user1,
            organization=self.organization,
            is_developer=True,
            email=f"otheruser@{self.domain}",
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.user1_membership.save()

        self.user2 = KippoUser(username="user2", github_login="user2", password="test", email="user2@github.com", is_staff=True)
        self.user2.save()
        self.user2_membership = OrganizationMembership(
            user=self.user2,
            organization=self.organization,
            is_developer=True,
            email=f"otheruser@{self.domain}",
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.user2_membership.save()

        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.project_start_date = timezone.datetime(2019, 6, 3).date()
        self.project_target_date = timezone.datetime(2019, 7, 3).date()
        self.kippoproject = KippoProject(
            name="testproject",
            organization=self.organization,
            start_date=self.project_start_date,
            target_date=self.project_target_date,
            columnset=columnset,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.kippoproject.save()
        active_states = self.kippoproject.columnset.get_active_column_names()
        active_state = active_states[0]

        task1 = KippoTask(
            title="task1",
            category="cat1",
            github_issue_api_url="http://github.com/task/1",
            project=self.kippoproject,
            assignee=self.user1,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task1.save()
        task1status = KippoTaskStatus(
            task=task1,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=2,
            state=active_state,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task1status.save()

        task2 = KippoTask(
            title="task2",
            category="cat2",
            github_issue_api_url="http://github.com/task/2",
            project=self.kippoproject,
            assignee=self.user1,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task2.save()
        task2status = KippoTaskStatus(
            task=task2,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=1,
            state=active_state,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task2status.save()
        self.user1effort_total = task1status.estimate_days + task2status.estimate_days

        task3 = KippoTask(
            title="task3",
            category="cat3",
            github_issue_api_url="http://github.com/task/3",
            project=self.kippoproject,
            assignee=self.user2,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task3.save()
        task3status = KippoTaskStatus(
            task=task3,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=5,
            state=active_state,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task3status.save()

        task4 = KippoTask(
            title="task4",
            category="cat4",
            github_issue_api_url="http://github.com/task/4",
            project=self.kippoproject,
            assignee=self.user2,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task4.save()
        task4status = KippoTaskStatus(
            task=task4,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=5,
            state=active_state,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task4status.save()
        self.user2effort_total = task3status.estimate_days + task4status.estimate_days

    def test_get_project_weekly_effort(self):
        assert KippoTaskStatus.objects.filter(task__project=self.kippoproject).count() == 4
        assert KippoTaskStatus.objects.filter(task__project=self.kippoproject, effort_date=timezone.datetime(2019, 6, 5).date()).count() == 4

        wednesday_weekday = 3
        date_keyed_status_entries = get_project_weekly_effort(
            project=self.kippoproject, current_date=timezone.datetime(2019, 6, 5).date(), representative_day=wednesday_weekday
        )
        self.assertTrue(date_keyed_status_entries)
        user_status = {}
        for period_date, status_entries in date_keyed_status_entries.items():
            for entry in status_entries:
                user = entry["task__assignee__github_login"]
                user_status[user] = {"task_count": entry["task_count"], "estimate_days_sum": entry["estimate_days_sum"]}
        self.assertTrue(user_status)
        self.assertEqual(user_status["user1"]["task_count"], 2)
        self.assertEqual(user_status["user1"]["estimate_days_sum"], self.user1effort_total)

        self.assertEqual(user_status["user2"]["task_count"], 2)
        self.assertEqual(user_status["user2"]["estimate_days_sum"], self.user2effort_total)

    def test_prepare_project_plot_data(self):
        target_current_date = timezone.datetime(2019, 6, 5).date()
        data, assignees, burndown_line = prepare_project_plot_data(self.kippoproject, current_date=target_current_date)
        self.assertTrue(data)
        effort_date_count = len(data["effort_date"])
        self.assertEqual(effort_date_count, 7)
        for assignee_key, assignee_data in data.items():
            if assignee_key != "effort_date":
                self.assertEqual(len(assignee_data), effort_date_count, data)

        self.assertTrue(assignees)

    def test_get_project_weekly_effort__with_kippomilestone(self):
        assert KippoMilestone.objects.count() == 0

        milestone_startdate = timezone.datetime(2019, 6, 1).date()
        milestone_enddate = timezone.datetime(2019, 6, 10).date()

        # create existing KippoMilestone/GithubMilestone
        kippo_milestone = KippoMilestone(project=self.kippoproject, title="milestone1", start_date=milestone_startdate, target_date=milestone_enddate)
        kippo_milestone.save()

        assert KippoTaskStatus.objects.filter(task__project=self.kippoproject).count() == 4
        assert KippoTaskStatus.objects.filter(task__project=self.kippoproject, effort_date=timezone.datetime(2019, 6, 5).date()).count() == 4

        wednesday_weekday = 3
        date_keyed_status_entries = get_project_weekly_effort(
            project=self.kippoproject, current_date=timezone.datetime(2019, 6, 5).date(), representative_day=wednesday_weekday
        )
        self.assertTrue(date_keyed_status_entries)
        user_status = {}
        for period_date, status_entries in date_keyed_status_entries.items():
            for entry in status_entries:
                user = entry["task__assignee__github_login"]
                user_status[user] = {"task_count": entry["task_count"], "estimate_days_sum": entry["estimate_days_sum"]}
        self.assertTrue(user_status)
        self.assertEqual(user_status["user1"]["task_count"], 2)
        self.assertEqual(user_status["user1"]["estimate_days_sum"], self.user1effort_total)

        self.assertEqual(user_status["user2"]["task_count"], 2)
        self.assertEqual(user_status["user2"]["estimate_days_sum"], self.user2effort_total)
