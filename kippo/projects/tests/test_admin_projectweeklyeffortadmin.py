from http import HTTPStatus

from accounts.models import KippoUser, OrganizationMembership
from common.tests import DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase, setup_basic_project
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.urls import reverse
from django.utils import timezone
from projects.admin import ProjectWeeklyEffortAdmin
from projects.functions import previous_week_startdate
from projects.models import KippoProject, ProjectWeeklyEffort

from .utils import MockRequest, reset_buckets


class ProjectWeeklyEffortAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        reset_buckets()
        super().setUp()
        created_objects = setup_basic_project(organization=self.organization)
        self.project1 = created_objects["KippoProject"]
        self.project2 = created_objects["KippoProject2"]

        self.staffuser_with_org2_username = "staffuser_with_org2"
        self.staffuser_with_org2 = KippoUser.objects.create(username=self.staffuser_with_org2_username, is_superuser=False, is_staff=True)

        # add membership
        membership = OrganizationMembership(
            user=self.staffuser_with_org2,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )
        membership.save()

        # create ProjectWeeklyEffortAdmin for each project
        # get mondays (week_start) from at least 3 months ago
        today = timezone.now()
        three_months_ago = timezone.now() - timezone.timedelta(days=3 * 30)
        current = three_months_ago
        MONDAY = 0
        while current <= today:
            if current.weekday() == MONDAY:
                for project in (self.project1, self.project2):
                    for user in (self.staffuser_with_org, self.staffuser_with_org2):
                        ProjectWeeklyEffort.objects.create(week_start=current, project=project, user=user, hours=5)
            current += timezone.timedelta(days=1)

        self.staffuser_with_org1_request = MockRequest()
        self.staffuser_with_org1_request.user = self.staffuser_with_org

        self.staffuser_with_org2_request = MockRequest()
        self.staffuser_with_org2_request.user = self.staffuser_with_org2

    def test_download_action(self):
        data = {
            "action": "download_csv",
            ACTION_CHECKBOX_NAME: [e.id for e in ProjectWeeklyEffort.objects.filter(project__organization__in=self.staffuser_with_org.organizations)],
        }
        change_url = reverse("admin:projects_projectweeklyeffort_changelist")
        self.client.force_login(self.staffuser_with_org)
        response = self.client.post(change_url, data, follow=True)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        expected = "/projects/download/"
        actual = response.redirect_chain[-1][0]
        self.assertTrue(actual.startswith(expected), f"actual({actual}) != expected({expected})")
