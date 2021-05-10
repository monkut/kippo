import datetime

from accounts.models import EmailDomain, KippoOrganization, KippoUser, OrganizationMembership
from common.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from django.utils import timezone
from tasks.models import KippoTask, KippoTaskStatus

from ..functions import get_kippoproject_taskstatus_csv_rows, previous_week_startdate


class ProjectsFunctionsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.cli_manager = KippoUser.objects.get(username="cli-manager")

        created_objects = setup_basic_project()
        self.user1 = created_objects["KippoUser"]

        # get active column state names
        self.project = created_objects["KippoProject"]
        active_state_names = self.project.get_active_column_names()

        self.organization = created_objects["KippoOrganization"]

        # create second user (not created in `setup_basic_project()`
        self.user2 = KippoUser(
            username="user2",
            github_login="user2",
            password="user2",
            email="user2@github.com",
            is_staff=True,
        )
        self.user2.save()

        user2_org_membership = OrganizationMembership(
            user=self.user2,
            organization=self.organization,
            is_developer=True,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        user2_org_membership.save()

        # create task status
        kippo_task1 = created_objects["KippoTask"]

        tz = timezone.get_current_timezone()
        first_effort_date = timezone.make_aware(timezone.datetime(2018, 9, 3), tz).date()  # monday
        self.kippotaskstatus1 = KippoTaskStatus(
            task=kippo_task1,
            state=active_state_names[0],
            effort_date=first_effort_date.strftime("%Y-%m-%d"),
            estimate_days=5,
            comment="status1-comment",
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.kippotaskstatus1.save()

        kippo_task2 = KippoTask(
            title="task2",
            category="development",
            project=self.project,
            assignee=self.user2,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
            github_issue_html_url="https://github.com/repos/octocat/Hello-World/issues/1348",
            github_issue_api_url="https://api.github.com/repos/octocat/Hello-World/issues/1348",
        )
        kippo_task2.save()

        self.kippotaskstatus2 = KippoTaskStatus(
            task=kippo_task2,
            state=active_state_names[0],
            effort_date=first_effort_date.strftime("%Y-%m-%d"),
            estimate_days=1,
            comment="status1-comment",
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.kippotaskstatus2.save()

    def test_get_kippoproject_taskstatus_csv(self):
        expected_headers = (
            "kippo_task_id",
            "kippo_milestone",
            "github_issue_html_url",
            "category",
            "effort_date",
            "state",
            "estimate_days",
            "assignee_github_login",
            "latest_comment",
            "labels",
        )
        # check rows with headers
        expected_row_count = 3
        project_taskstatus_csv_row_generator = get_kippoproject_taskstatus_csv_rows(self.project, with_headers=True)
        actual_rows = list(project_taskstatus_csv_row_generator)
        self.assertEqual(len(actual_rows), expected_row_count, f"actual_rows: {actual_rows}")

        # check that the rows contain the expected number of values
        self.assertTrue(all(len(row) == len(expected_headers) for row in actual_rows))

        # check rows without headers
        expected_row_count = 2
        project_taskstatus_csv_row_generator = get_kippoproject_taskstatus_csv_rows(self.project, with_headers=False)
        actual_rows = list(project_taskstatus_csv_row_generator)
        self.assertTrue(len(actual_rows) == expected_row_count)

        # check that the rows contain the expected number of values
        self.assertTrue(all(len(row) == len(expected_headers) for row in actual_rows))

    def test_previous_week_startdate__monday(self):
        today = datetime.date(2021, 5, 10)  # monday
        expected = datetime.date(2021, 5, 3)  # previous week's monday
        actual = previous_week_startdate(today=today)
        self.assertEqual(actual, expected)

    def test_previous_week_startdate__tuesday(self):
        today = datetime.date(2021, 5, 11)  # tuesday
        expected = datetime.date(2021, 5, 3)  # previous week's monday
        actual = previous_week_startdate(today=today)
        self.assertEqual(actual, expected)

    def test_previous_week_startdate__sunday(self):
        today = datetime.date(2021, 5, 9)  # sunday
        expected = datetime.date(2021, 5, 3)  # previous week's monday
        actual = previous_week_startdate(today=today)
        self.assertEqual(actual, expected)
