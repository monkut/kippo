import urllib.parse
from http import HTTPStatus
from uuid import uuid4

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from common.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import Client, TestCase
from kippo.aws import s3_key_exists
from projects.functions import generate_projectstatuscomments_csv, generate_projectweeklyeffort_csv, previous_week_startdate
from projects.models import KippoProjectStatus, ProjectWeeklyEffort

from .utils import reset_buckets


class DownloadViewTestCase(TestCase):
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

        self.client = Client()
        # create ProjectWeeklyEffort
        ProjectWeeklyEffort.objects.create(project=self.project, week_start=previous_week_startdate(), user=self.user, hours=5)

        # create KippoProjectStatus
        KippoProjectStatus.objects.create(project=self.project, created_by=self.user, updated_by=self.user, comment="this is a comment")

    def test_data_download_waiter__generate_projectweeklyeffort_csv(self):
        key = "tmp/download/{}.csv".format(str(uuid4()))
        generate_projectweeklyeffort_csv(user_id=str(self.user.id), key=key)
        assert s3_key_exists(settings.DUMPDATA_S3_BUCKETNAME, key=key)

        urlencoded_key = urllib.parse.quote_plus(key)
        url = f"/projects/download/?filename={urlencoded_key}"
        self.client.force_login(self.user)

        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

        # update request referer to /projects/download/
        # - force redirect
        client = Client(HTTP_REFERER="/projects/download/")
        client.force_login(self.user)
        response = client.get(url, follow=True)
        expected = f"/projects/download/done/?filename={urlencoded_key}"
        self.assertRedirects(response, expected_url=expected)

        # update request referer to /projects/download/done/
        # - force redirect
        client = Client(HTTP_REFERER="/projects/download/done/")
        client.force_login(self.user)
        url = f"/projects/download/done/?filename={urlencoded_key}"
        response = client.get(url, follow=True)
        # django client redirects to root url, causing 404
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
        # redirect_url = response.redirect_chain[-1][0]
        # response = requests.get(redirect_url)
        # self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_data_download_waiter__generate_projectstatuscomments_csv(self):
        key = "tmp/download/{}.csv".format(str(uuid4()))

        generate_projectstatuscomments_csv(project_ids=[str(self.project.id)], key=key)
        assert s3_key_exists(settings.DUMPDATA_S3_BUCKETNAME, key=key)

        urlencoded_key = urllib.parse.quote_plus(key)
        url = f"/projects/download/?filename={urlencoded_key}"
        self.client.force_login(self.user)

        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

        # update request referer to /projects/download/
        # - force redirect
        client = Client(HTTP_REFERER="/projects/download/")
        client.force_login(self.user)
        response = client.get(url, follow=True)
        expected = f"/projects/download/done/?filename={urlencoded_key}"
        self.assertRedirects(response, expected_url=expected)

        # update request referer to /projects/download/done/
        # - force redirect
        client = Client(HTTP_REFERER="/projects/download/done/")
        client.force_login(self.user)
        url = f"/projects/download/done/?filename={urlencoded_key}"
        response = client.get(url, follow=True)
        # django client redirects to root url, causing 404
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
