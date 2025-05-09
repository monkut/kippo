import json
from pathlib import Path

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_COLUMNSET_PK
from django.test import TestCase
from django.utils import timezone
from ghorgs.wrappers import GithubIssue
from octocat.models import GithubAccessToken, GithubRepository, GithubRepositoryLabelSet
from projects.models import ActiveKippoProject, KippoProject, ProjectColumnSet

from ..models import KippoTask, KippoTaskStatus
from ..periodic.tasks import OrganizationIssueProcessor, get_existing_kippo_project

DEFAULT_GITHUB_PROJECT_URL = "https://github.com/ghdummyorg/reponame/"
TESTDATA_DIRECTORY = Path(__file__).parent / "testdata"


class GithubOrganizationProjectMock:
    def __init__(self, html_url: str = DEFAULT_GITHUB_PROJECT_URL) -> None:
        self.html_url = html_url


def load_json_to_githubissue(json_filepath: Path):
    """Convert a given Github Issue JSON representation to a ghorgs.wrappers.GithubIssue"""
    with json_filepath.open("r", encoding="utf8") as json_in:
        issue_json = json_in.read()
    # GithubIssue.from_dict() alone does not perform nested conversion, using json
    issue = json.loads(issue_json, object_hook=GithubIssue.from_dict)
    return issue


class PeriodicTaskFunctionsTestCase(TestCase):
    fixtures = [
        "default_columnset",
        "required_bot_users",
    ]

    def setUp(self):
        self.target_github_project_html_url = DEFAULT_GITHUB_PROJECT_URL
        self.other_github_project_html_url = "https://github.com/other/repo/"
        now = timezone.now()
        start_date = (now - timezone.timedelta(days=7)).date()
        end_date = (now + timezone.timedelta(days=7)).date()

        github_manager_user = KippoUser.objects.get(username="github-manager")
        dummy_organization = KippoOrganization(
            name="dummy-org",
            github_organization_name="ghdummyorg",
            created_by=github_manager_user,
            updated_by=github_manager_user,
        )
        dummy_organization.save()

        default_columnset = ProjectColumnSet.objects.get(id=DEFAULT_COLUMNSET_PK)

        # create closed project
        self.closed_project = KippoProject(
            organization=dummy_organization,
            name="closed-project-A",
            created_by=github_manager_user,
            updated_by=github_manager_user,
            is_closed=True,
            github_project_html_url=self.target_github_project_html_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset,
        )
        self.closed_project.save()

        # create opened project
        self.opened_project = KippoProject(
            organization=dummy_organization,
            name="opened-project-A",
            created_by=github_manager_user,
            updated_by=github_manager_user,
            is_closed=False,
            github_project_html_url=self.target_github_project_html_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset,
        )
        self.opened_project.save()

        # create opened un-linked project
        self.other_opened_project = KippoProject(
            organization=dummy_organization,
            name="other-opened-project-A",
            created_by=github_manager_user,
            updated_by=github_manager_user,
            is_closed=False,
            github_project_html_url=self.other_github_project_html_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset,
        )

    def test_get_existing_kippo_project(self):
        github_project = GithubOrganizationProjectMock(html_url=DEFAULT_GITHUB_PROJECT_URL)

        existing_open_projects = list(ActiveKippoProject.objects.all())
        related_kippo_project = get_existing_kippo_project(github_project, existing_open_projects)
        self.assertTrue(related_kippo_project.pk == self.opened_project.pk)


class OrganizationIssueProcessorTestCase(TestCase):
    fixtures = [
        "default_columnset",
        "required_bot_users",
        "default_labelset",
    ]

    def setUp(self):
        self.target_github_project_html_url = DEFAULT_GITHUB_PROJECT_URL
        self.other_github_project_html_url = "https://github.com/other/repo/"
        now = timezone.now()
        start_date = (now - timezone.timedelta(days=7)).date()
        end_date = (now + timezone.timedelta(days=7)).date()

        self.github_manager_user = KippoUser.objects.get(username="github-manager")
        self.organization = KippoOrganization(
            name="dummy-org",
            github_organization_name="myorg",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        self.organization.save()

        self.user1 = KippoUser(
            username="user1",
            github_login="user1",
            password="test",  # noqa: S106
            email="user1@github.com",
            is_staff=True,
        )
        self.user1.save()

        orgmembership = OrganizationMembership(
            user=self.user1,
            organization=self.organization,
            is_developer=True,
            created_by=self.user1,
            updated_by=self.user1,
        )
        orgmembership.save()

        token = GithubAccessToken(
            organization=self.organization,
            token="abcdefABCDEF1234567890",  # noqa: S106
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        token.save()

        default_columnset = ProjectColumnSet.objects.get(id=DEFAULT_COLUMNSET_PK)

        # create closed project
        self.closed_project = KippoProject(
            organization=self.organization,
            name="closed-project-A",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
            is_closed=True,
            github_project_html_url=self.target_github_project_html_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset,
        )
        self.closed_project.save()

        # create opened project
        self.opened_project = KippoProject(
            organization=self.organization,
            name="opened-project-A",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
            is_closed=False,
            github_project_html_url=self.target_github_project_html_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset,
        )
        self.opened_project.save()

        # create opened un-linked project
        self.other_opened_project = KippoProject(
            organization=self.organization,
            name="other-opened-project-A",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
            is_closed=False,
            github_project_html_url=self.other_github_project_html_url,
            start_date=start_date,
            target_date=end_date,
            actual_date=end_date,
            columnset=default_columnset,
        )
        self.other_opened_project.save()

        default_labelset = GithubRepositoryLabelSet.objects.all()[0]
        GithubRepository.objects.create(
            organization=self.organization,
            name="myrepo",
            label_set=default_labelset,
            api_url="https://api.github.com/repos/myorg/myrepo",
            html_url="https://github.com/myorg/myrepo",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        KippoTaskStatus.objects.all().delete()
        KippoTask.objects.all().delete()

    def test_organizationissueprocessor___init__(self):
        issue_processor = OrganizationIssueProcessor(
            organization=self.organization,
            status_effort_date=timezone.datetime(2019, 6, 5).date(),
            github_project_html_urls=[self.target_github_project_html_url],
        )
        self.assertTrue(issue_processor)

    def test_process_new_task(self):
        issue_processor = OrganizationIssueProcessor(
            organization=self.organization,
            status_effort_date=timezone.datetime(2019, 6, 5).date(),
            github_project_html_urls=[self.target_github_project_html_url],
        )
        json_filepath = TESTDATA_DIRECTORY / "issue.json"
        github_issue = load_json_to_githubissue(json_filepath)
        is_new_task, new_taskstatus_objects, updated_taskstatus_objects = issue_processor.process(self.opened_project, github_issue)
        self.assertTrue(is_new_task)

        taskstatus = KippoTaskStatus.objects.all()[0]
        expected = "planning"
        actual = taskstatus.state
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        # check task updated properly
        task = KippoTask.objects.all()[0]
        expected = "json issue title"
        actual = task.title
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        expected = "json issue body"
        actual = task.description
        self.assertTrue(actual == expected)

    def test_process_existing_task(self):
        # create existing KippoTask & KippoTaskStatus matching sample 'issue.json'
        task = KippoTask.objects.create(
            title="existing task title",
            category="some category",
            project=self.opened_project,
            assignee=self.user1,
            github_issue_api_url="https://api.github.com/repos/myorg/myrepo/issues/9",
            github_issue_html_url="https://github.com/myorg/myrepo/issues/9",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        taskstatus = KippoTaskStatus.objects.create(
            task=task,
            state="in-progress",
            effort_date=timezone.datetime(2019, 6, 5).date(),
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )

        issue_processor = OrganizationIssueProcessor(
            organization=self.organization,
            status_effort_date=timezone.datetime(2019, 6, 5).date(),
            github_project_html_urls=[self.target_github_project_html_url],
        )
        json_filepath = TESTDATA_DIRECTORY / "issue.json"
        github_issue = load_json_to_githubissue(json_filepath)
        # update state
        expected = "in-review"
        github_issue.project_column = expected
        is_new_task, new_taskstatus_objects, updated_taskstatus_objects = issue_processor.process(self.opened_project, github_issue)
        self.assertFalse(is_new_task)

        taskstatus.refresh_from_db()
        actual = taskstatus.state
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        # check task updated properly
        task.refresh_from_db()
        expected = "json issue title"
        actual = task.title
        self.assertTrue(actual == expected)

        expected = "json issue body"
        actual = task.description
        self.assertTrue(actual == expected)
