import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from django.utils import timezone
from ghorgs.exceptions import GithubGraphQLError
from projects.models import KippoMilestone
from tasks.models import KippoTask, KippoTaskStatus

from ..functions import (
    GithubWebhookProcessor,
    _escape_graphql_string,
    copy_project_v2,
    create_project_v2,
    get_kippomilestone_from_github_issue,
    get_organization_id,
    get_organization_projects_v2,
)
from ..models import GithubMilestone, GithubRepository

assert os.getenv("KIPPO_TESTING", None)  # The KIPPO_TESTING environment variable must be set to True

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
        self.secret_encoded = self.organization.github_webhook_secret.encode("utf8")
        self.project = results["KippoProject"]
        self.user1 = results["KippoUser"]
        self.githubrepo = results["GithubRepository"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # create user2 for task assignement check
        self.user2 = KippoUser(username="octocat2", github_login="octocat2", password="test", email="octocat2@github.com", is_staff=True)  # noqa: S106
        self.user2.save()

        orgmembership = OrganizationMembership(
            user=self.user2,
            organization=self.organization,
            is_developer=True,
            created_by=self.user2,
            updated_by=self.user2,
        )
        orgmembership.save()
        self.current_date = timezone.now().date()

        # remove existing task/taskstatus
        KippoTaskStatus.objects.all().delete()
        KippoTask.objects.all().delete()

    def test_get_kippomilestone_from_github_issue__without__githubmilestone(self):
        assert GithubRepository.objects.count() == 1

        github_issue = GITHUBAPI_ISSUE_NO_MILESTONE
        result = get_kippomilestone_from_github_issue(github_issue, organization=self.organization)
        self.assertIsNone(result)
        assert GithubRepository.objects.count() == 1

    def test_get_kippomilestone_from_github_issue__with__githubmilestone__with__githubrepository(self):
        # expect that githubmilestone will be created
        assert GithubRepository.objects.count() == 1
        assert GithubMilestone.objects.count() == 0

        # create existing github entry, to confirm if milestone is created
        repo_html_url = "https://github.com/octocat/Hello-World"
        repo_api_url = "https://api.github.com/repos/octocat/Hello-World"
        name = "Hello-World"
        repo = GithubRepository(
            organization=self.organization,
            name=name,
            label_set=self.organization.default_labelset,
            api_url=repo_api_url,
            html_url=repo_html_url,
        )
        repo.save()

        github_issue = GITHUBAPI_ISSUE
        result = get_kippomilestone_from_github_issue(github_issue, organization=self.organization)
        self.assertIsNone(result)

        # confirm that githubmilestone is created
        result = GithubMilestone.objects.filter(repository=repo)
        self.assertTrue(result)
        expected = 1
        self.assertEqual(len(result), expected)

    def test_get_kippomilestone_from_github_issue__githubmilestone__with__kippomilestone(self):
        github_issue = GITHUBAPI_ISSUE
        result = get_kippomilestone_from_github_issue(github_issue, organization=self.organization)
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

        result = get_kippomilestone_from_github_issue(github_issue, organization=self.organization)
        self.assertEqual(result, kippomilestone_1)


class ProjectsV2FunctionsTestCase(TestCase):
    """Test cases for GitHub ProjectsV2 GraphQL functions."""

    @patch("octocat.functions.run_graphql_request")
    def test_get_organization_id(self, mock_graphql: MagicMock) -> None:
        """Test getting organization node ID."""
        mock_graphql.return_value = {"data": {"organization": {"id": "O_kgDOBxxxxxx"}}}

        result = get_organization_id("test-org", "fake-token")

        self.assertEqual(result, "O_kgDOBxxxxxx")
        mock_graphql.assert_called_once()
        call_args = mock_graphql.call_args
        self.assertIn("test-org", call_args[0][0])
        self.assertEqual(call_args[0][1], "fake-token")
        self.assertTrue(call_args[1]["raise_on_error"])

    @patch("octocat.functions.run_graphql_request")
    def test_get_organization_id_error(self, mock_graphql: MagicMock) -> None:
        """Test handling error when organization not found."""
        mock_graphql.side_effect = GithubGraphQLError("Organization not found")

        with self.assertRaises(GithubGraphQLError):
            get_organization_id("nonexistent-org", "fake-token")

    @patch("octocat.functions.run_graphql_request")
    def test_get_organization_projects_v2(self, mock_graphql: MagicMock) -> None:
        """Test listing organization ProjectsV2 projects (templates only by default)."""
        mock_graphql.return_value = {
            "data": {
                "organization": {
                    "projectsV2": {
                        "nodes": [
                            {
                                "id": "PVT_kwDOBxx1",
                                "title": "Template 1",
                                "url": "https://github.com/orgs/test-org/projects/1",
                                "number": 1,
                                "template": True,
                            },
                            {
                                "id": "PVT_kwDOBxx2",
                                "title": "Template 2",
                                "url": "https://github.com/orgs/test-org/projects/2",
                                "number": 2,
                                "template": True,
                            },
                            {
                                "id": "PVT_kwDOBxx3",
                                "title": "Regular Project",
                                "url": "https://github.com/orgs/test-org/projects/3",
                                "number": 3,
                                "template": False,
                            },
                        ]
                    }
                }
            }
        }

        result = get_organization_projects_v2("test-org", "fake-token")

        # Should only return templates (2 out of 3)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "PVT_kwDOBxx1")
        self.assertEqual(result[0]["title"], "Template 1")
        self.assertEqual(result[1]["id"], "PVT_kwDOBxx2")
        self.assertEqual(result[1]["title"], "Template 2")

    @patch("octocat.functions.run_graphql_request")
    def test_copy_project_v2(self, mock_graphql: MagicMock) -> None:
        """Test copying a ProjectsV2 template."""
        mock_graphql.return_value = {
            "data": {
                "copyProjectV2": {
                    "projectV2": {
                        "id": "PVT_kwDOBxxNew",
                        "title": "New Project",
                        "url": "https://github.com/orgs/test-org/projects/3",
                        "number": 3,
                    }
                }
            }
        }

        result = copy_project_v2("PVT_kwDOBxxTemplate", "O_kgDOBxxxxxx", "New Project", "fake-token")

        self.assertEqual(result["id"], "PVT_kwDOBxxNew")
        self.assertEqual(result["title"], "New Project")
        self.assertEqual(result["url"], "https://github.com/orgs/test-org/projects/3")
        self.assertEqual(result["number"], 3)
        mock_graphql.assert_called_once()
        call_args = mock_graphql.call_args
        self.assertIn("copyProjectV2", call_args[0][0])
        self.assertIn("PVT_kwDOBxxTemplate", call_args[0][0])
        self.assertIn("O_kgDOBxxxxxx", call_args[0][0])
        self.assertIn("New Project", call_args[0][0])

    def test_escape_graphql_string(self) -> None:
        """Test GraphQL string escaping handles all control characters."""
        # Test backslashes (must be escaped first)
        self.assertEqual(_escape_graphql_string("path\\to\\file"), "path\\\\to\\\\file")

        # Test double quotes
        self.assertEqual(_escape_graphql_string('say "hello"'), 'say \\"hello\\"')

        # Test newlines
        self.assertEqual(_escape_graphql_string("line1\nline2"), "line1\\nline2")

        # Test carriage returns
        self.assertEqual(_escape_graphql_string("line1\rline2"), "line1\\rline2")

        # Test tabs
        self.assertEqual(_escape_graphql_string("col1\tcol2"), "col1\\tcol2")

        # Test backspace and form feed
        self.assertEqual(_escape_graphql_string("text\b"), "text\\b")
        self.assertEqual(_escape_graphql_string("text\f"), "text\\f")

        # Test combined special characters
        self.assertEqual(
            _escape_graphql_string('Project "Test"\nwith newline'),
            'Project \\"Test\\"\\nwith newline',
        )

    @patch("octocat.functions.run_graphql_request")
    def test_copy_project_v2_escapes_special_chars(self, mock_graphql: MagicMock) -> None:
        """Test that special characters in title are properly escaped."""
        mock_graphql.return_value = {
            "data": {
                "copyProjectV2": {
                    "projectV2": {
                        "id": "PVT_kwDOBxxNew",
                        "title": 'Project "with" quotes\nand newline',
                        "url": "https://github.com/orgs/test-org/projects/3",
                        "number": 3,
                    }
                }
            }
        }

        result = copy_project_v2("PVT_kwDOBxxTemplate", "O_kgDOBxxxxxx", 'Project "with" quotes\nand newline', "fake-token")

        self.assertEqual(result["title"], 'Project "with" quotes\nand newline')
        call_args = mock_graphql.call_args
        # Verify quotes and newlines are escaped in the GraphQL query
        self.assertIn('\\"with\\"', call_args[0][0])
        self.assertIn("\\n", call_args[0][0])

    @patch("octocat.functions.run_graphql_request")
    def test_create_project_v2(self, mock_graphql: MagicMock) -> None:
        """Test creating a blank ProjectsV2 project."""
        mock_graphql.return_value = {
            "data": {
                "createProjectV2": {
                    "projectV2": {
                        "id": "PVT_kwDOBxxBlank",
                        "title": "Blank Project",
                        "url": "https://github.com/orgs/test-org/projects/4",
                        "number": 4,
                    }
                }
            }
        }

        result = create_project_v2("O_kgDOBxxxxxx", "Blank Project", "fake-token")

        self.assertEqual(result["id"], "PVT_kwDOBxxBlank")
        self.assertEqual(result["title"], "Blank Project")
        self.assertEqual(result["url"], "https://github.com/orgs/test-org/projects/4")
        self.assertEqual(result["number"], 4)
        mock_graphql.assert_called_once()
        call_args = mock_graphql.call_args
        self.assertIn("createProjectV2", call_args[0][0])
        self.assertIn("O_kgDOBxxxxxx", call_args[0][0])
        self.assertIn("Blank Project", call_args[0][0])

    @patch("octocat.functions.run_graphql_request")
    def test_create_project_v2_error(self, mock_graphql: MagicMock) -> None:
        """Test handling error when project creation fails."""
        mock_graphql.side_effect = GithubGraphQLError("Permission denied")

        with self.assertRaises(GithubGraphQLError):
            create_project_v2("O_kgDOBxxxxxx", "New Project", "fake-token")
