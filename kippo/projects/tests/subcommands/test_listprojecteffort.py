from unittest import mock

from accounts.models import OrganizationMembership, SlackCommand
from commons.slackcommand import get_all_subcommands
from commons.tests import IsStaffModelAdminTestCaseBase, setup_basic_project
from commons.tests.utils import webhook_response_factory

from projects.functions import previous_week_startdate
from projects.models import ProjectWeeklyEffort
from projects.slackcommand.subcommands.listprojecteffort import ListProjectEffortSubCommand


class ListProjectEffortSubCommandTestCase(IsStaffModelAdminTestCaseBase):
    def setUp(self):
        super().setUp()

        # populate slack related settings
        self.organization.slack_api_token = "xoxb-1234567890-1234567890123-1234567890123-abcde"  # noqa: S105
        self.organization.slack_signing_secret = "1234567890123"  # noqa: S105
        self.organization.slack_command_name = "kippo"
        self.organization.slack_attendance_report_channel = "#kippo"
        self.organization.enable_slack_channel_reporting = True
        self.organization.save()

        # update slack user id
        self.staffuser_with_org_slack_id = "U12345678"
        self.staffuser_with_org_slack_username = "testuser"

        membership = OrganizationMembership.objects.get(organization=self.organization, user=self.staffuser_with_org)
        membership.slack_user_id = self.staffuser_with_org_slack_id
        membership.slack_username = self.staffuser_with_org_slack_username
        membership.save()

        created = setup_basic_project(organization=self.organization)
        self.project = created["KippoProject"]

        self.project_slack_channel_name = "test_channel"

    @mock.patch("projects.slackcommand.subcommands.listprojecteffort.WebhookClient.send", return_value=webhook_response_factory())
    def test_no_related_effort(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_projectweeklyeffort_count = 0
        project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
        assert project_weekly_effort_count == expected_projectweeklyeffort_count

        subcommand_text = ListProjectEffortSubCommand.DISPLAY_COMMAND_NAME
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command=ListProjectEffortSubCommand.DISPLAY_COMMAND_NAME,
            text=subcommand_text,
            response_url="https://example.com/response_url",
            payload={"channel_name": "unlinked_channel"},
        )
        command.save()

        blocks, web_response, webhook_response = ListProjectEffortSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_block_count = 1  # response content only
        self.assertEqual(len(blocks), expected_block_count)

    @mock.patch("projects.slackcommand.subcommands.listprojectstatus.WebhookClient.send", return_value=webhook_response_factory())
    def test_related_project__with_projectweeklyeffort(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_projectweeklyeffort_count = 0
        project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
        assert project_weekly_effort_count == expected_projectweeklyeffort_count

        # create a ProjectWeeklyEffort for the project
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            hours=10,
            user=self.staffuser_with_org,
            created_by=self.staffuser_with_org,
            updated_by=self.staffuser_with_org,
            week_start=previous_week_startdate(),
        )

        subcommand_text = ListProjectEffortSubCommand.DISPLAY_COMMAND_NAME
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command=ListProjectEffortSubCommand.DISPLAY_COMMAND_NAME,
            text=subcommand_text,
            response_url="https://example.com/response_url",
            payload={"channel_name": "unlinked_channel"},
        )
        command.save()

        blocks, web_response, webhook_response = ListProjectEffortSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_block_count = 1
        self.assertEqual(len(blocks), expected_block_count)

    def test_subcommand_registered(self):
        """Confirm that the subcommand is registered."""
        available_subcommands = get_all_subcommands()
        self.assertIn(ListProjectEffortSubCommand, available_subcommands)
