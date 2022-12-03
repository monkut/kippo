import json
from pathlib import Path

from accounts.models import KippoOrganization, KippoUser
from common.tests import DEFAULT_COLUMNSET_PK
from django.test import TestCase
from django.utils import timezone
from ghorgs.wrappers import GithubIssue
from projects.models import KippoProject, ProjectColumnSet

from ..handlers.clean import delete
from ..models import KippoTask, KippoTaskStatus

DEFAULT_GITHUB_PROJECT_URL = "https://github.com/ghdummyorg/reponame/"
TESTDATA_DIRECTORY = Path(__file__).parent / "testdata"


class GithubOrganizationProjectMock:
    def __init__(self, html_url=DEFAULT_GITHUB_PROJECT_URL):
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

        self.github_manager_user = KippoUser.objects.get(username="github-manager")
        dummy_organization = KippoOrganization(
            name="dummy-org",
            github_organization_name="ghdummyorg",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        dummy_organization.save()

        default_columnset = ProjectColumnSet.objects.get(id=DEFAULT_COLUMNSET_PK)

        # create closed project
        self.closed_project = KippoProject(
            organization=dummy_organization,
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
            organization=dummy_organization,
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
            organization=dummy_organization,
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

    def test_delete_open_project(self):
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
        KippoTaskStatus.objects.create(
            task=task,
            state="in-progress",
            effort_date=timezone.datetime(2019, 6, 5).date(),
            created_datetime=timezone.datetime(2019, 6, 5),
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        assert KippoTaskStatus.objects.count() == 1
        delete({}, {})
        expected = 1
        actual = KippoTaskStatus.objects.count()
        self.assertEqual(actual, expected)

    def test_delete_closed_project(self):
        # create existing KippoTask & KippoTaskStatus matching sample 'issue.json'
        task = KippoTask.objects.create(
            title="existing task title",
            category="some category",
            project=self.closed_project,
            assignee=self.user1,
            github_issue_api_url="https://api.github.com/repos/myorg/myrepo/issues/9",
            github_issue_html_url="https://github.com/myorg/myrepo/issues/9",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        KippoTaskStatus.objects.create(
            task=task,
            state="in-progress",
            effort_date=timezone.datetime(2019, 6, 5).date(),
            created_datetime=timezone.datetime(2019, 6, 5),
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        assert KippoTaskStatus.objects.count() == 1
        delete({}, {})
        expected = 0
        actual = KippoTaskStatus.objects.count()
        self.assertEqual(actual, expected)
