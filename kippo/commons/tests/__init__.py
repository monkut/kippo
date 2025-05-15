from accounts.models import EmailDomain, KippoOrganization, KippoUser, OrganizationMembership, PersonalHoliday
from django.test import TestCase
from django.utils import timezone
from octocat.models import GithubAccessToken, GithubRepository
from projects.models import KippoProject, ProjectColumnSet
from tasks.models import KippoTask

from ..admin import KippoAdminSite


DEFAULT_FIXTURES = ["required_bot_users", "default_columnset", "default_labelset"]

DEFAULT_COLUMNSET_PK = "414e69c8-8ea3-4c9c-8129-6f5aac108fa2"


def setup_basic_project(
    organization: KippoOrganization | None = None,
    repository_name: str = "Hello-World",
    github_project_api_id: str = "2640902",
    column_info: list[dict] | None = None,
):
    if not column_info:
        # example content:
        # [
        # {'id': 'MDEzOlByb2plY3RDb2x1bW42MTE5AZQ1', 'name': 'in-progress', 'resourcePath': '/orgs/myorg/projects/21/columns/6119645'},
        # ]
        column_info = [
            {
                "id": "MDEzOlByb2plY3RDb2x1bW42MTE5AZQ1",
                "name": "in-progress",
                "resourcePath": "/orgs/myorg/projects/21/columns/6119645",
            },
            {
                "id": "MDEzOlByb2plY3RDb2x1bW42MXXX5AZQ1",
                "name": "in-review",
                "resourcePath": "/orgs/myorg/projects/21/columns/2803722",
            },
        ]

    created_objects = {}
    user = KippoUser(username="octocat", github_login="octocat", password="test", email="a@github.com", is_staff=True)  # noqa: S106
    user.save()
    created_objects["KippoUser"] = user
    if not organization:
        organization = KippoOrganization(name="myorg-full", github_organization_name="myorg", day_workhours=8, created_by=user, updated_by=user)
        organization.save()
    created_objects["KippoOrganization"] = organization

    email_domain = EmailDomain(organization=organization, domain="github.com", is_staff_domain=True, created_by=user, updated_by=user)
    email_domain.save()
    created_objects["EmailDomain"] = email_domain

    orgmembership = OrganizationMembership(user=user, organization=organization, is_developer=True, created_by=user, updated_by=user)
    orgmembership.save()
    created_objects["OrganizationMembership"] = orgmembership

    access_token = GithubAccessToken(organization=organization, token="kdakkfj", created_by=user, updated_by=user)  # noqa: S106
    access_token.save()
    created_objects["GithubAccessToken"] = access_token

    default_columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)

    kippo_project = KippoProject(
        organization=organization,
        name="octocat-test-project",
        github_project_html_url=f"https://github.com/orgs/{organization.github_organization_name}/projects/1",
        github_project_api_url=f"https://api.github.com/projects/{github_project_api_id}",
        columnset=default_columnset,
        column_info=column_info,
        created_by=user,
        updated_by=user,
    )
    kippo_project.save()
    created_objects["KippoProject"] = kippo_project

    github_project2_api_id = "1234567"
    kippo_project2 = KippoProject(
        organization=organization,
        name="octocat-test-project2",
        github_project_html_url=f"https://github.com/orgs/{organization.github_organization_name}/projects/2",
        github_project_api_url=f"https://api.github.com/projects/{github_project2_api_id}",
        columnset=default_columnset,
        column_info=column_info,
        created_by=user,
        updated_by=user,
    )
    kippo_project2.save()
    created_objects["KippoProject2"] = kippo_project2

    github_repo = GithubRepository(
        organization=organization,
        name=repository_name,
        api_url=f"https://api.github.com/repos/{organization.github_organization_name}/{repository_name}",
        html_url=f"https://github.com/{organization.github_organization_name}/{repository_name}",
        created_by=user,
        updated_by=user,
    )
    github_repo.save()
    created_objects["GithubRepository"] = github_repo

    kippo_task = KippoTask(
        title="githubcodesorg test task-1",
        category="test category",
        project=kippo_project,
        assignee=user,
        created_by=user,
        updated_by=user,
        github_issue_html_url=f"https://github.com/{organization.github_organization_name}/{repository_name}/issues/1347",
        github_issue_api_url=f"https://api.github.com/repos/{organization.github_organization_name}/{repository_name}/issues/1347",
    )
    kippo_task.save()
    created_objects["KippoTask"] = kippo_task

    return created_objects


class MockRequest:
    pass


class IsStaffModelAdminTestCaseBase(TestCase):
    fixtures = ["required_bot_users", "default_columnset", "default_labelset"]

    def setUp(self):
        self.github_manager = KippoUser.objects.get(username="github-manager")
        self.organization = KippoOrganization.objects.create(
            name="test-organization",
            github_organization_name="isstaffmodeladmintestcasebase-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.organization_domain = "testorg.com"
        self.email_domain = EmailDomain.objects.create(
            organization=self.organization,
            domain=self.organization_domain,
            is_staff_domain=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_organization = KippoOrganization.objects.create(
            name="other-test-organization",
            github_organization_name="isstaffmodeladmintestcasebase-other-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_organization_domain = "othertestorg.com"
        self.email_domain = EmailDomain.objects.create(
            organization=self.organization,
            domain=self.other_organization_domain,
            is_staff_domain=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # create superuser and related request mock
        self.superuser_username = "superuser_no_org"
        self.superuser_no_org = KippoUser.objects.create(username=self.superuser_username, is_superuser=True, is_staff=True)
        self.super_user_request = MockRequest()
        self.super_user_request.user = self.superuser_no_org

        # create staff user and related request mock
        self.staffuser_username = "staffuser_with_org"
        self.staffuser_with_org = KippoUser.objects.create(username=self.staffuser_username, is_superuser=False, is_staff=True)
        PersonalHoliday.objects.create(user=self.staffuser_with_org, day=(timezone.now() + timezone.timedelta(days=5)).date())
        # add membership
        membership = OrganizationMembership(
            user=self.staffuser_with_org,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )
        membership.save()

        self.staff_user_request = MockRequest()
        self.staff_user_request.user = self.staffuser_with_org

        # create staff user and related request mock
        self.otherstaffuser_username = "otherstaffuser_with_org"
        self.otherstaffuser_with_org = KippoUser.objects.create(username=self.otherstaffuser_username, is_superuser=False, is_staff=True)
        PersonalHoliday.objects.create(user=self.otherstaffuser_with_org, day=(timezone.now() + timezone.timedelta(days=5)).date())
        # add membership
        membership = OrganizationMembership(
            user=self.otherstaffuser_with_org,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        membership.save()

        self.otherstaff_user_request = MockRequest()
        self.otherstaff_user_request.user = self.otherstaffuser_with_org

        # create staff user with no org and related request mock
        self.staffuser2_username = "staffuser_no_org"
        self.staffuser2_no_org = KippoUser.objects.create(username=self.staffuser2_username, is_superuser=False, is_staff=True)
        PersonalHoliday.objects.create(user=self.staffuser2_no_org, day=(timezone.now() + timezone.timedelta(days=5)).date())
        self.staff_user2_request = MockRequest()
        self.staff_user2_request.user = self.staffuser2_no_org

        self.site = KippoAdminSite()
