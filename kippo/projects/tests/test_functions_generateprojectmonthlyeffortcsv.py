import datetime

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from commons.tests.utils import reset_buckets
from django.conf import settings
from django.test import TestCase

from kippo.awsclients import download_s3_csv, s3_key_exists
from projects.functions import generate_projectmonthlyeffort_csv, previous_week_startdate
from projects.models import ProjectWeeklyEffort


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
            password="test",  # noqa: S106
            email="noorguser@github.com",
            is_staff=True,
        )
        self.no_org_user.save()

        # create ProjectMonthlyEffort
        self.previous_week_date = previous_week_startdate()
        # define ProjectWeeklyEffort objects
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=datetime.date(2023, 7, 3), user=self.user, hours=70)
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=datetime.date(2023, 7, 31), user=self.user, hours=35)
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=datetime.date(2023, 8, 7), user=self.user, hours=70)
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=datetime.date(2023, 8, 28), user=self.user, hours=35)

    def test_generate_projectmonthlyeffort_csv(self):
        key = "tmp/test/test.csv"
        ids = list(ProjectWeeklyEffort.objects.all().values_list("id", flat=True))
        generate_projectmonthlyeffort_csv(user_id=str(self.user.id), key=key, effort_ids=ids)

        self.assertTrue(s3_key_exists(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key))

        rows = download_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key)
        expected = 3
        self.assertEqual(len(rows), expected)

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
