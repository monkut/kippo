"""Tests for the 振り返り従業員アンケート Slack request (service + KippoProjectAdmin action)."""

from unittest import mock

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.contrib import admin
from django.contrib.messages import constants as message_levels
from django.test import TestCase
from django.utils import timezone
from slack_sdk.errors import SlackApiError

from projects.admin import request_employee_survey_action
from projects.models import KippoProject, KippoProjectUserStatisfactionResult, ProjectWeeklyEffort
from projects.services.employee_survey_request import ProjectEmployeeSurveyRequestManager


class EmployeeSurveyRequestManagerTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.organization.slack_api_token = "xoxb-test-token"  # noqa: S105
        self.organization.slack_channel_name = "#kippo"
        self.organization.save()
        self.project: KippoProject = created["KippoProject"]
        self.other_project: KippoProject = created["KippoProject2"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        self.worker = self._add_member("survey-worker", slack_user_id="U0WORKER")
        self._log_effort(self.project, self.worker)

    def _add_member(self, username: str, *, slack_user_id: str = "") -> KippoUser:
        user = KippoUser.objects.create(username=username, email=f"{username}@github.com")
        OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            slack_user_id=slack_user_id,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        return user

    def _log_effort(self, project: KippoProject, user: KippoUser) -> ProjectWeeklyEffort:
        return ProjectWeeklyEffort.objects.create(
            project=project,
            user=user,
            hours=8,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def _respond(self, project: KippoProject, user: KippoUser) -> KippoProjectUserStatisfactionResult:
        return KippoProjectUserStatisfactionResult.objects.create(
            project=project,
            fullfillment_score=3,
            growth_score=3,
            created_by=user,
            updated_by=user,
        )

    def test_init_requires_slack_channel_name(self):
        self.organization.slack_channel_name = ""
        with self.assertRaises(ValueError):
            ProjectEmployeeSurveyRequestManager(self.organization)

    def test_init_requires_slack_api_token(self):
        self.organization.slack_api_token = ""
        with self.assertRaises(ValueError):
            ProjectEmployeeSurveyRequestManager(self.organization)

    def test_pending_users_are_those_with_logged_effort(self):
        no_effort_user = self._add_member("survey-no-effort")
        pending = ProjectEmployeeSurveyRequestManager.get_pending_users(self.project)
        self.assertIn(self.worker, pending)
        self.assertNotIn(no_effort_user, pending)

    def test_pending_users_exclude_effort_on_another_project(self):
        other_worker = self._add_member("survey-other-project-worker")
        self._log_effort(self.other_project, other_worker)
        self.assertNotIn(other_worker, ProjectEmployeeSurveyRequestManager.get_pending_users(self.project))

    def test_pending_users_exclude_already_responded(self):
        self._respond(self.project, self.worker)
        self.assertNotIn(self.worker, ProjectEmployeeSurveyRequestManager.get_pending_users(self.project))

    def test_pending_users_deduplicated_across_multiple_effort_weeks(self):
        effort = ProjectWeeklyEffort.objects.filter(project=self.project, user=self.worker).first()
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.worker,
            week_start=effort.week_start - timezone.timedelta(days=7),
            hours=4,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        pending = ProjectEmployeeSurveyRequestManager.get_pending_users(self.project)
        self.assertEqual(pending.count(self.worker), 1)

    def test_pending_users_exclude_unassigned_sentinel_user(self):
        unassigned = KippoUser.objects.get(username__startswith=settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX)
        self._log_effort(self.project, unassigned)
        self.assertNotIn(unassigned, ProjectEmployeeSurveyRequestManager.get_pending_users(self.project))

    def test_survey_url_preselects_the_project(self):
        url = ProjectEmployeeSurveyRequestManager.survey_url(self.project)
        self.assertTrue(url.startswith(settings.HOST_URL))
        self.assertIn("/admin/projects/kippoprojectuserstatisfactionresult/add/", url)
        self.assertTrue(url.endswith(f"?project={self.project.id}"))

    @mock.patch("projects.services.employee_survey_request.WebClient.chat_postMessage", return_value={"ok": True})
    def test_post_mentions_pending_users_by_slack_id(self, mock_post: mock.MagicMock):
        manager = ProjectEmployeeSurveyRequestManager(self.organization)
        posted_users = manager.post(self.project)

        self.assertEqual(posted_users, [self.worker])
        mock_post.assert_called_once()
        _, post_kwargs = mock_post.call_args
        self.assertEqual(post_kwargs["channel"], "#kippo")
        rendered = " ".join(block["text"]["text"] for block in post_kwargs["blocks"])
        self.assertIn("<@U0WORKER>", rendered)
        self.assertIn(self.project.name, rendered)
        self.assertIn(ProjectEmployeeSurveyRequestManager.survey_url(self.project), rendered)

    @mock.patch("projects.services.employee_survey_request.WebClient.chat_postMessage", return_value={"ok": True})
    def test_post_falls_back_to_display_name_without_slack_user_id(self, mock_post: mock.MagicMock):
        no_slack_user = self._add_member("survey-no-slack")
        self._log_effort(self.project, no_slack_user)
        ProjectEmployeeSurveyRequestManager(self.organization).post(self.project)

        _, post_kwargs = mock_post.call_args
        rendered = " ".join(block["text"]["text"] for block in post_kwargs["blocks"])
        self.assertIn(no_slack_user.display_name.strip(), rendered)

    @mock.patch("projects.services.employee_survey_request.WebClient.chat_postMessage", return_value={"ok": True})
    def test_post_skipped_when_nobody_is_pending(self, mock_post: mock.MagicMock):
        self._respond(self.project, self.worker)
        self.assertEqual(ProjectEmployeeSurveyRequestManager(self.organization).post(self.project), [])
        mock_post.assert_not_called()


class MockMessagesRequest:
    """Minimal request for driving an admin action; collects message_user() calls."""

    def __init__(self, user: KippoUser) -> None:
        self.user = user
        self.GET = {}
        self.POST = {}


class RequestEmployeeSurveyActionTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.organization.slack_api_token = "xoxb-test-token"  # noqa: S105
        self.organization.slack_channel_name = "#kippo"
        self.organization.save()
        self.project: KippoProject = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        self.worker = KippoUser.objects.create(username="action-worker", email="action-worker@github.com")
        OrganizationMembership.objects.create(
            user=self.worker,
            organization=self.organization,
            slack_user_id="U0ACTION",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.worker,
            hours=8,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.modeladmin = mock.MagicMock(spec=admin.ModelAdmin)
        self.request = MockMessagesRequest(self.github_manager)

    def _messages(self, level: int) -> list[str]:
        return [str(call.args[1]) for call in self.modeladmin.message_user.call_args_list if call.kwargs.get("level") == level]

    def _run(self) -> None:
        request_employee_survey_action(self.modeladmin, self.request, KippoProject.objects.filter(pk=self.project.pk))

    def test_action_is_registered_on_the_project_admins(self):
        from projects.admin import ActiveKippoProjectAdmin, KippoProjectAdmin

        self.assertIn(request_employee_survey_action, KippoProjectAdmin.actions)
        self.assertIn(request_employee_survey_action, ActiveKippoProjectAdmin.actions)

    @mock.patch("projects.services.employee_survey_request.WebClient.chat_postMessage", return_value={"ok": True})
    def test_posts_and_reports_success(self, mock_post: mock.MagicMock):
        self._run()
        mock_post.assert_called_once()
        self.assertTrue(any(self.project.name in message for message in self._messages(message_levels.INFO)))
        self.assertEqual(self._messages(message_levels.ERROR), [])

    @mock.patch("projects.services.employee_survey_request.WebClient.chat_postMessage")
    def test_errors_when_kippo_slack_channel_is_not_configured(self, mock_post: mock.MagicMock):
        self.organization.slack_channel_name = ""
        self.organization.save()
        self._run()

        mock_post.assert_not_called()
        errors = self._messages(message_levels.ERROR)
        self.assertEqual(len(errors), 1)
        self.assertIn("no kippo Slack channel", errors[0])
        self.assertIn(self.project.name, errors[0])

    @mock.patch("projects.services.employee_survey_request.WebClient.chat_postMessage")
    def test_errors_when_slack_api_token_is_not_configured(self, mock_post: mock.MagicMock):
        self.organization.slack_api_token = ""
        self.organization.save()
        self._run()

        mock_post.assert_not_called()
        errors = self._messages(message_levels.ERROR)
        self.assertEqual(len(errors), 1)
        self.assertIn("no Slack API token", errors[0])

    @mock.patch("projects.services.employee_survey_request.WebClient.chat_postMessage")
    def test_reports_slack_api_error(self, mock_post: mock.MagicMock):
        mock_post.side_effect = SlackApiError("failed", response={"ok": False, "error": "channel_not_found"})
        self._run()

        errors = self._messages(message_levels.ERROR)
        self.assertEqual(len(errors), 1)
        self.assertIn("channel_not_found", errors[0])

    @mock.patch("projects.services.employee_survey_request.WebClient.chat_postMessage", return_value={"ok": True})
    def test_warns_when_no_members_are_pending(self, mock_post: mock.MagicMock):
        KippoProjectUserStatisfactionResult.objects.create(
            project=self.project,
            fullfillment_score=3,
            growth_score=3,
            created_by=self.worker,
            updated_by=self.worker,
        )
        self._run()

        mock_post.assert_not_called()
        warnings = self._messages(message_levels.WARNING)
        self.assertEqual(len(warnings), 1)
        self.assertIn(self.project.name, warnings[0])
