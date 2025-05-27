from unittest import mock

from accounts.models import OrganizationMembership, SlackCommand
from commons.tests import IsStaffModelAdminTestCaseBase, setup_basic_project
from commons.tests.utils import webhook_response_factory

from projects.models import KippoProjectStatus
from projects.slackcommand.subcommands.projectstatus import ProjectStatusSubCommand


class ProjectStatusSubCommandTestCase(IsStaffModelAdminTestCaseBase):
    def setUp(self):
        super().setUp()

        # populate slack related settings
        self.organization.slack_api_token = "xoxb-1234567890-1234567890123-1234567890123-abcde"  # noqa: S105
        self.organization.slack_signing_secret = "1234567890123"  # noqa: S105
        self.organization.slack_command_name = "kippo"
        self.organization.slack_attendance_report_channel = "#kippo"
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

    @mock.patch("projects.slackcommand.subcommands.projectstatus.WebhookClient.send", return_value=webhook_response_factory())
    def test_no_linkied_project(self, *_):
        """Confirm that a *new* KippoProojectStatus is NOT created when a project with related slack channel is not found."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_kippoprojectstatus_count = 0
        assert KippoProjectStatus.objects.count() == expected_kippoprojectstatus_count
        expected_linked_kippoproject_count = 0
        actual_linked_kippoproject_count = KippoProjectStatus.objects.filter(project__slack_channel_name=self.project_slack_channel_name).count()
        assert actual_linked_kippoproject_count == expected_linked_kippoproject_count

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="projectstatus",
            text="projectstatus hello this is my update",
            response_url="https://example.com/response_url",
            payload={"channel_name": self.project_slack_channel_name},
        )
        command.save()

        blocks, web_response, webhook_response = ProjectStatusSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_kippoprojectstatus_count = 0
        actual_kippoprojectstatus_count = KippoProjectStatus.objects.count()
        self.assertEqual(actual_kippoprojectstatus_count, expected_kippoprojectstatus_count)

    def test_closed_project(self):
        """Confirm that a *new* KippoProojectStatus is NOT created when the related project is closed."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_kippoprojectstatus_count = 0
        assert KippoProjectStatus.objects.count() == expected_kippoprojectstatus_count
        expected_linked_kippoproject_count = 0
        actual_linked_kippoproject_count = KippoProjectStatus.objects.filter(project__slack_channel_name=self.project_slack_channel_name).count()
        assert actual_linked_kippoproject_count == expected_linked_kippoproject_count

        # link project with slack_channel_name
        self.project.slack_channel_name = self.project_slack_channel_name
        self.project.is_closed = True
        self.project.save()

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="projectstatus",
            text="projectstatus hello this is my update",
            response_url="https://example.com/response_url",
            payload={"channel_name": self.project_slack_channel_name},
        )
        command.save()

        blocks, web_response, webhook_response = ProjectStatusSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_kippoprojectstatus_count = 0
        actual_kippoprojectstatus_count = KippoProjectStatus.objects.count()
        self.assertEqual(actual_kippoprojectstatus_count, expected_kippoprojectstatus_count)

    def test_valid_projectstatus_subcommand_aliases(self):
        """Confirm that a *new* KippoProojectStatus is created for the related project."""
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

        status_comment = "hello this is my update"
        for alias in ProjectStatusSubCommand.ALIASES:
            subcommand_text = f"{alias} {status_comment}"
            command = SlackCommand(
                organization=self.organization,
                user=self.staffuser_with_org,
                sub_command=alias,
                text=subcommand_text,
                response_url="https://example.com/response_url",
                payload={"channel_name": self.project_slack_channel_name},
            )
            command.save()

            blocks, web_response, webhook_response = ProjectStatusSubCommand.handle(command)
            self.assertTrue(blocks)
            self.assertFalse(web_response)
            self.assertTrue(webhook_response)

            expected_kippoprojectstatus_count = 1
            actual_kippoprojectstatus_count = KippoProjectStatus.objects.filter(project=self.project).count()
            self.assertEqual(actual_kippoprojectstatus_count, expected_kippoprojectstatus_count)

            kippoprojectstatus = KippoProjectStatus.objects.filter(project=self.project).first()
            self.assertEqual(kippoprojectstatus.comment, status_comment)

            # delete the created record for the next test iteration
            kippoprojectstatus.delete()
