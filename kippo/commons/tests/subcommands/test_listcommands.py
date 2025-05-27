from unittest import mock

from accounts.models import OrganizationMembership, SlackCommand

from commons.slackcommand import get_all_subcommands
from commons.slackcommand.subcommands.listcommands import ListCommandsSubCommand
from commons.tests import IsStaffModelAdminTestCaseBase
from commons.tests.utils import webhook_response_factory


class ProjectStatusSubCommandTestCase(IsStaffModelAdminTestCaseBase):
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

    @mock.patch("projects.slackcommand.subcommands.listprojectstatus.WebhookClient.send", return_value=webhook_response_factory())
    def test_no_related_project(self, *_):
        """Confirm that a *new* KippoProojectStatus is created for the related project."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count

        subcommand_text = "list-project-status"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="list-commands",
            text=subcommand_text,
            response_url="https://example.com/response_url",
            payload={"channel_name": "unlinked_channel"},
        )
        command.save()

        blocks, web_response, webhook_response = ListCommandsSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        supported_commands = get_all_subcommands()
        expected_block_count = len(supported_commands)
        self.assertEqual(len(blocks), expected_block_count)
