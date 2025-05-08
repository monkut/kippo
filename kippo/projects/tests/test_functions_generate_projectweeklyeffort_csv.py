from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from commons.tests.utils import reset_buckets
from django.conf import settings
from django.test import Client, TestCase
from django.utils import timezone

from kippo.awsclients import download_s3_csv, s3_key_exists
from projects.functions import generate_projectweeklyeffort_csv, previous_week_startdate
from projects.models import ProjectWeeklyEffort


class GenerateProjectWeeklyEffortCsvTestCase(TestCase):
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
            user=self.user,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
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

        self.client = Client()
        # create ProjectWeeklyEffort
        self.previous_week_date = previous_week_startdate()
        older = self.previous_week_date - timezone.timedelta(days=7)
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=self.previous_week_date, user=self.user, hours=5)
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=older, user=self.user, hours=5)

    def test_generate_projectweeklyeffort_csv(self):
        key = "tmp/test/test.csv"
        generate_projectweeklyeffort_csv(user_id=str(self.user.id), key=key)

        self.assertTrue(s3_key_exists(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key))

        rows = download_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key)
        expected = 2
        self.assertEqual(len(rows), expected, rows)

        key = "tmp/test/other.csv"
        from_datetime = self.previous_week_date
        generate_projectweeklyeffort_csv(user_id=str(self.user.id), key=key, from_datetime_isoformat=from_datetime.isoformat())
        self.assertTrue(s3_key_exists(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key))

        rows = download_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key)
        expected = 1
        self.assertEqual(len(rows), expected, rows)
