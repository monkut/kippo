from unittest import mock

from accounts.models import OrganizationMembership, SlackCommand
from commons.slackcommand import get_all_subcommands
from commons.tests import IsStaffModelAdminTestCaseBase, setup_basic_project
from commons.tests.utils import webhook_response_factory

from projects.models import KippoProjectStatus, ProjectWeeklyEffort
from projects.slackcommand.subcommands.projecteffort import ProjectEffortSubCommand


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
        expected_projectweeklyeffort_count = 0
        project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
        assert project_weekly_effort_count == expected_projectweeklyeffort_count

        valid_subcommand_text = f"{ProjectEffortSubCommand.DISPLAY_COMMAND_NAME} 10"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command=ProjectEffortSubCommand.DISPLAY_COMMAND_NAME,
            text=valid_subcommand_text,
            response_url="https://example.com/response_url",
            payload={"channel_name": self.project_slack_channel_name},
        )
        command.save()

        blocks, web_response, webhook_response = ProjectEffortSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_weekly_effort_count = 0
        project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
        self.assertEqual(project_weekly_effort_count, expected_weekly_effort_count)

    def test_valid_with_subcommand_aliases(self):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_projectweeklyeffort_count = 0
        project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
        assert project_weekly_effort_count == expected_projectweeklyeffort_count

        # link project with slack_channel_name
        self.project.slack_channel_name = self.project_slack_channel_name
        self.project.is_closed = False
        self.project.save()

        weekly_effort_hours = 10
        for alias in ProjectEffortSubCommand.ALIASES:
            valid_subcommand_text = f"{alias} {weekly_effort_hours}"
            command = SlackCommand(
                organization=self.organization,
                user=self.staffuser_with_org,
                sub_command=alias,
                text=valid_subcommand_text,
                response_url="https://example.com/response_url",
                payload={"channel_name": self.project_slack_channel_name},
            )
            command.save()

            blocks, web_response, webhook_response = ProjectEffortSubCommand.handle(command)
            self.assertTrue(blocks)
            self.assertFalse(web_response)
            self.assertTrue(webhook_response)

            expected_weekly_effort_count = 1
            project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
            self.assertEqual(project_weekly_effort_count, expected_weekly_effort_count)

            project_weekly_effort = ProjectWeeklyEffort.objects.first()
            self.assertTrue(project_weekly_effort)

            self.assertEqual(project_weekly_effort.hours, weekly_effort_hours)

            # delete the created record for the next test iteration
            project_weekly_effort.delete()

    def test_invalid_hours_input(self):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_projectweeklyeffort_count = 0
        project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
        assert project_weekly_effort_count == expected_projectweeklyeffort_count

        # link project with slack_channel_name
        self.project.slack_channel_name = self.project_slack_channel_name
        self.project.is_closed = False
        self.project.save()

        exceed_max = 7 * 24 + 1  # 7 days * 24 hours + 1 hour
        for invalid_weekly_effort_values in (" XX other", "XXY", str(exceed_max)):
            invalid_subcommand_text = f"{ProjectEffortSubCommand.DISPLAY_COMMAND_NAME}{invalid_weekly_effort_values}"
            command = SlackCommand(
                organization=self.organization,
                user=self.staffuser_with_org,
                sub_command=ProjectEffortSubCommand.DISPLAY_COMMAND_NAME,
                text=invalid_subcommand_text,
                response_url="https://example.com/response_url",
                payload={"channel_name": self.project_slack_channel_name},
            )
            command.save()

            blocks, web_response, webhook_response = ProjectEffortSubCommand.handle(command)
            self.assertTrue(blocks)
            self.assertFalse(web_response)
            self.assertTrue(webhook_response)

            expected_weekly_effort_count = 0
            project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
            self.assertEqual(project_weekly_effort_count, expected_weekly_effort_count)

    def test_entry_already_exists(self):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_projectweeklyeffort_count = 0
        project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
        assert project_weekly_effort_count == expected_projectweeklyeffort_count

        # link project with slack_channel_name
        self.project.slack_channel_name = self.project_slack_channel_name
        self.project.is_closed = False
        self.project.save()

        valid_effort_hours = 10
        valid_subcommand_text = f"{ProjectEffortSubCommand.DISPLAY_COMMAND_NAME} {valid_effort_hours}"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command=ProjectEffortSubCommand.DISPLAY_COMMAND_NAME,
            text=valid_subcommand_text,
            response_url="https://example.com/response_url",
            payload={"channel_name": self.project_slack_channel_name},
        )
        command.save()

        blocks, web_response, webhook_response = ProjectEffortSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_weekly_effort_count = 1
        project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
        assert project_weekly_effort_count == expected_weekly_effort_count

        # try to create the same entry again
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command=ProjectEffortSubCommand.DISPLAY_COMMAND_NAME,
            text=valid_subcommand_text,
            response_url="https://example.com/response_url",
            payload={"channel_name": self.project_slack_channel_name},
        )
        command.save()
        blocks, web_response, webhook_response = ProjectEffortSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_weekly_effort_count = 1
        project_weekly_effort_count = ProjectWeeklyEffort.objects.filter(project=self.project).count()
        self.assertEqual(project_weekly_effort_count, expected_weekly_effort_count)

    def test_subcommand_registered(self):
        """Confirm that the subcommand is registered."""
        available_subcommands = get_all_subcommands()
        self.assertIn(ProjectEffortSubCommand, available_subcommands)
