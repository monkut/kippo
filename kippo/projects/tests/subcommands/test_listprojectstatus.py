from unittest import mock

from accounts.models import OrganizationMembership, SlackCommand
from commons.tests import IsStaffModelAdminTestCaseBase, setup_basic_project
from commons.tests.utils import webhook_response_factory
from django.utils import timezone

from projects.models import KippoProjectStatus
from projects.slackcommand.subcommands.listprojectstatus import ListProjectStatusSubCommand


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

        created = setup_basic_project(organization=self.organization)
        self.project = created["KippoProject"]

        self.project_slack_channel_name = "test_channel"
        KippoProjectStatus.objects.all().delete()

    @mock.patch("projects.slackcommand.subcommands.listprojectstatus.WebhookClient.send", return_value=webhook_response_factory())
    def test_no_related_project(self, *_):
        """Confirm that a *new* KippoProojectStatus is created for the related project."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_kippoprojectstatus_count = 0
        assert KippoProjectStatus.objects.count() == expected_kippoprojectstatus_count
        expected_linked_kippoproject_count = 0
        actual_linked_kippoproject_count = KippoProjectStatus.objects.filter(project__slack_channel_name=self.project_slack_channel_name).count()
        assert actual_linked_kippoproject_count == expected_linked_kippoproject_count

        subcommand_text = "list-project-status"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="list-project-status",
            text=subcommand_text,
            response_url="https://example.com/response_url",
            payload={"channel_name": "unlinked_channel"},
        )
        command.save()

        blocks, web_response, webhook_response = ListProjectStatusSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_block_count = 1
        self.assertEqual(len(blocks), expected_block_count)

        # check content of the first block
        block = blocks[0]

        expected_type = "section"
        self.assertEqual(block["type"], expected_type)
        self.assertIn("プロジェクトの`slack_channel_name`設定を確認してください", block["text"]["text"])

    @mock.patch("projects.slackcommand.subcommands.listprojectstatus.WebhookClient.send", return_value=webhook_response_factory())
    def test_related_project__without_kippoprojectstatus(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_kippoprojectstatus_count = 0
        assert KippoProjectStatus.objects.count() == expected_kippoprojectstatus_count
        expected_linked_kippoproject_count = 0
        actual_linked_kippoproject_count = KippoProjectStatus.objects.filter(project__slack_channel_name=self.project_slack_channel_name).count()
        assert actual_linked_kippoproject_count == expected_linked_kippoproject_count

        # link project with slack_channel_name
        self.project.slack_channel_name = self.project_slack_channel_name
        self.project.is_closed = False
        self.project.save()

        subcommand_text = "list-project-status"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="list-project-status",
            text=subcommand_text,
            response_url="https://example.com/response_url",
            payload={"channel_name": self.project_slack_channel_name},
        )
        command.save()

        blocks, web_response, webhook_response = ListProjectStatusSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_block_count = 4
        self.assertEqual(len(blocks), expected_block_count)

        # check content of the first block
        header_block = blocks[0]
        divider_block_1 = blocks[1]
        project_block = blocks[2]
        divider_block_2 = blocks[3]

        expected_type = "header"
        self.assertEqual(header_block["type"], expected_type)

        expected_type = "divider"
        self.assertEqual(divider_block_1["type"], expected_type)

        expected_type = "section"
        self.assertEqual(project_block["type"], expected_type)
        self.assertIn("なし", project_block["text"]["text"])

        expected_type = "divider"
        self.assertEqual(divider_block_2["type"], expected_type)

    @mock.patch("projects.slackcommand.subcommands.listprojectstatus.WebhookClient.send", return_value=webhook_response_factory())
    def test_related_project__with_kippoprojectstatus(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_kippoprojectstatus_count = 0
        assert KippoProjectStatus.objects.count() == expected_kippoprojectstatus_count
        expected_linked_kippoproject_count = 0
        actual_linked_kippoproject_count = KippoProjectStatus.objects.filter(project__slack_channel_name=self.project_slack_channel_name).count()
        assert actual_linked_kippoproject_count == expected_linked_kippoproject_count

        # link project with slack_channel_name
        self.project.slack_channel_name = self.project_slack_channel_name
        self.project.is_closed = False
        self.project.save()

        # create KippoProjectStatus for the project
        kippoprojectstatus_comment = "This is a test status comment."
        kippo_project_status = KippoProjectStatus(
            project=self.project,
            created_by=self.staffuser_with_org,
            updated_by=self.staffuser_with_org,
            comment=kippoprojectstatus_comment,
        )
        kippo_project_status.save()
        localnow = timezone.localtime()
        KippoProjectStatus.objects.filter(pk=kippo_project_status.pk).update(
            created_datetime=localnow,
            updated_datetime=localnow,
        )
        kippo_project_status.refresh_from_db()

        subcommand_text = "list-project-status"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="list-project-status",
            text=subcommand_text,
            response_url="https://example.com/response_url",
            payload={"channel_name": self.project_slack_channel_name},
        )
        command.save()

        blocks, web_response, webhook_response = ListProjectStatusSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_block_count = 4
        self.assertEqual(len(blocks), expected_block_count)

        # check content of the first block
        header_block = blocks[0]
        divider_block_1 = blocks[1]
        project_block = blocks[2]
        divider_block_2 = blocks[3]

        expected_type = "header"
        self.assertEqual(header_block["type"], expected_type)

        expected_type = "divider"
        self.assertEqual(divider_block_1["type"], expected_type)

        expected_type = "section"
        self.assertEqual(project_block["type"], expected_type)
        self.assertIn(kippoprojectstatus_comment, project_block["text"]["text"])

        expected_type = "divider"
        self.assertEqual(divider_block_2["type"], expected_type)
