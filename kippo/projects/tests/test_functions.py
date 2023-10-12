import datetime
from random import choice, randint

from accounts.functions import get_personal_holidays_generator
from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from common.tests import DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase, setup_basic_project
from django.db import connection, models, transaction
from django.test import TestCase
from octocat.tests.test_admin import DEFAULT_COLUMNSET_PK
from tasks.models import KippoTask, KippoTaskStatus

from kippo import settings
from kippo.aws import download_s3_csv, s3_key_exists

from ..functions import generate_projectmonthlyeffort_csv, get_kippoproject_taskstatus_csv_rows, logger, previous_week_startdate
from ..models import KippoProject, ProjectColumnSet, ProjectWeeklyEffort
from .utils import reset_buckets


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


from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from common.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from projects.functions import generate_projectmonthlyeffort_csv, previous_week_startdate
from projects.models import ProjectWeeklyEffort

from kippo.aws import download_s3_csv, s3_key_exists

from .utils import reset_buckets


class GenerateProjectMonthlyEffortCsvTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        reset_buckets()
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.project = created["KippoProject"]
        self.user = created["KippoUser"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        self.other_organization = KippoOrganization.objects.create(
            name="other-test-organization",
            github_organization_name="isstaffmodeladmintestcasebase-other-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # add membership
        membership = OrganizationMembership(
            user=self.user, organization=self.other_organization, created_by=self.github_manager, updated_by=self.github_manager, is_developer=True
        )
        membership.save()
        self.nonmember_organization = KippoOrganization.objects.create(
            name="nonmember-test-organization",
            github_organization_name="isstaffmodeladmintestcasebase-nonmember-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.no_org_user = KippoUser(
            username="noorguser",
            github_login="noorguser",
            password="test",
            email="noorguser@github.com",
            is_staff=True,
        )
        self.no_org_user.save()

        # create ProjectMonthlyEffort
        self.previous_week_date = previous_week_startdate()
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=datetime.date(2023, 7, 3), user=self.user, hours=70)
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=datetime.date(2023, 7, 31), user=self.user, hours=35)
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=datetime.date(2023, 8, 7), user=self.user, hours=70)
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=datetime.date(2023, 8, 28), user=self.user, hours=35)

    def test_generate_projectmonthlyeffort_csv(self):
        key = "tmp/test/test.csv"
        queryset_count = ProjectWeeklyEffort.objects.all().count()
        test_queryset = list(range(int(queryset_count) + 1))
        generate_projectmonthlyeffort_csv(user_id=str(self.user.id), key=key, effort_ids=test_queryset)

        self.assertTrue(s3_key_exists(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key))

        rows = download_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key)
        expected = 3

        self.assertEqual(len(rows), expected, rows)

        hours_value_1 = float(rows[0]["totalhours"])
        expected_hours_1 = 75  # 7/3の週のeffort:70h + 7/30の週のeffort1日分: 35/7 = 5h で75h
        self.assertEqual(hours_value_1, expected_hours_1)

        hours_value_2 = float(rows[1]["totalhours"])
        expected_hours_2 = 120
        # 7/31の週のeffort6日分: 35*6/7 = 30h
        # 8/7の週のeffort = 70h
        # 8/28の週のeffort4日分: 35*4/7 = 20h
        # 30h + 70h + 20h = 120h
        self.assertEqual(hours_value_2, expected_hours_2)

        hours_value_3 = float(rows[2]["totalhours"])
        expected_hours_3 = 15  # 8/28の週のeffort3日分: 35*3/7 = 15h
        self.assertEqual(hours_value_3, expected_hours_3)
