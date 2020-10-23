import json
import os
from pathlib import Path
from unittest import mock

from accounts.models import KippoUser, OrganizationMembership
from common.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from django.utils import timezone
from projects.models import KippoMilestone
from tasks.models import KippoTask, KippoTaskStatus

from ..functions import GithubWebhookProcessor, get_kippomilestone_from_github_issue
from ..models import GithubMilestone, GithubWebhookEvent
from .utils import load_webhookevent

assert os.getenv("KIPPO_TESTING", False)  # The KIPPO_TESTING environment variable must be set to True

TESTDATA_DIRECTORY = Path(__file__).parent / "testdata"
GITHUBAPI_ISSUE_JSON = {"issue": json.loads((TESTDATA_DIRECTORY / "github_api_issue.json").read_text(encoding="utf8"))}
GITHUBAPI_ISSUE = GithubWebhookProcessor._load_event_to_githubissue(GITHUBAPI_ISSUE_JSON)
GITHUBAPI_ISSUE_NO_MILESTONE_JSON = {"issue": json.loads((TESTDATA_DIRECTORY / "github_api_issue__no_milestone.json").read_text(encoding="utf8"))}
GITHUBAPI_ISSUE_NO_MILESTONE = GithubWebhookProcessor._load_event_to_githubissue(GITHUBAPI_ISSUE_NO_MILESTONE_JSON)


class OctocatFunctionsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.repository_name = "myrepo"
        results = setup_basic_project(repository_name=self.repository_name)

        self.organization = results["KippoOrganization"]
        self.secret_encoded = self.organization.webhook_secret.encode("utf8")
        self.project = results["KippoProject"]
        self.user1 = results["KippoUser"]
        self.githubrepo = results["GithubRepository"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # create user2 for task assignement check
        self.user2 = KippoUser(username="octocat2", github_login="octocat2", password="test", email="octocat2@github.com", is_staff=True)
        self.user2.save()

        orgmembership = OrganizationMembership(
            user=self.user2, organization=self.organization, is_developer=True, created_by=self.user2, updated_by=self.user2
        )
        orgmembership.save()
        self.current_date = timezone.now().date()

        # remove existing task/taskstatus
        KippoTaskStatus.objects.all().delete()
        KippoTask.objects.all().delete()

    def test_get_kippomilestone_from_github_issue__without__githubmilestone(self):
        github_issue = GITHUBAPI_ISSUE_NO_MILESTONE
        result = get_kippomilestone_from_github_issue(github_issue)
        self.assertIsNone(result)

    def test_get_kippomilestone_from_github_issue__githubmilestone__with__kippomilestone(self):
        github_issue = GITHUBAPI_ISSUE
        result = get_kippomilestone_from_github_issue(github_issue)
        self.assertIsNone(result)

        # create related KippoMilestone
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # create realted githubmilestone
        github_milestone = GithubMilestone(
            milestone=kippomilestone_1,
            repository=self.githubrepo,
            number=1,
            api_url="https://api.github.com/repos/octocat/Hello-World/milestones/1",
            html_url="https://github.com/octocat/Hello-World/milestones/v1.0",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        github_milestone.save()

        result = get_kippomilestone_from_github_issue(github_issue)
        self.assertEqual(result, kippomilestone_1)
