from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from common.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from projects.models import KippoMilestone
from tasks.models import KippoTaskStatus


class SetOrganizationTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
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

    def test_set_organization__valid_user(self):
        url = f"{settings.URL_PREFIX}/projects/set/organization/{self.organization.id}/"
        self.client.force_login(self.user)
        response = self.client.get(url)
        expected = HTTPStatus.FOUND
        actual = response.status_code
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")
        self.assertTrue(self.client.session["organization_id"] == str(self.organization.id))

    def test_set_organization__valid_user_nonmember_org(self):
        url = f"{settings.URL_PREFIX}/projects/set/organization/{self.nonmember_organization.id}/"
        self.client.force_login(self.user)
        response = self.client.get(url)
        expected = HTTPStatus.FOUND
        actual = response.status_code
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        actual = self.client.session["organization_id"]
        self.assertTrue(actual != str(self.nonmember_organization.id))
        self.assertTrue(actual == str(self.user.organizations[0].id))

    def test_set_organization__user_no_org(self):
        url = f"{settings.URL_PREFIX}/projects/set/organization/{self.nonmember_organization.id}/"
        self.client.force_login(self.no_org_user)
        response = self.client.get(url)
        expected = HTTPStatus.BAD_REQUEST
        actual = response.status_code
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        actual = self.client.session.get("organization_id", None)
        self.assertTrue(actual is None)


class ProjectMilestonesTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.task = created["KippoTask"]
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
        self.planning_column_name = "planning"
        self.client = Client()

        # set start_date, target_date for project
        self.project.start_date = timezone.datetime(2020, 9, 1).date()
        self.project.target_date = timezone.datetime(2020, 11, 1).date()
        self.project.save()

        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        self.kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            is_completed=False,
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        self.kippomilestone_1.save()
        self.firstdate = timezone.datetime(2020, 9, 2).date()

    def test_view_milestone_status__no_kippotaskstatus(self):
        self.client.force_login(self.user)

        url = reverse("view_milestone_status")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_view_milestone_status__with_kippotaskstatus(self):
        self.client.force_login(self.user)

        url = reverse("view_milestone_status")
        # create KippoTaskStatus object and confirm 200 is returned as expected
        # create existing taskstatus
        self.task1_status1 = KippoTaskStatus(
            task=self.task,
            state=self.planning_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status1.save()
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_view_milestone_status__with_milestone_id(self):
        assert KippoMilestone.objects.filter(id=self.kippomilestone_1.id).exists()
        self.client.force_login(self.user)
        # create KippoTaskStatus object and confirm 200 is returned as expected
        # create existing taskstatus
        self.task1_status1 = KippoTaskStatus(
            task=self.task,
            state=self.planning_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status1.save()

        assert self.kippomilestone_1.project.organization.id
        session = self.client.session  # *MUST* pull out 'session' as variable to update
        session.update({"organization_id": str(self.kippomilestone_1.project.organization.id)})
        session.save()
        url = reverse("view_milestone_status")
        url = f"{url}{self.kippomilestone_1.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK, response.content)
