from http import HTTPStatus
from unittest import mock

from commons.tests import IsStaffModelAdminTestCaseBase
from slack_sdk.webhook import WebhookResponse

from accounts.models import AttendanceRecord, OrganizationMembership, SlackCommand
from accounts.slackcommand.managers import AttendanceSlackManager
from accounts.slackcommand.subcommands import CheckInSubCommand


def webhook_response_factory(
    status_code: int = HTTPStatus.OK,
) -> WebhookResponse:
    """Create a webhook response."""
    return WebhookResponse(
        url="https://example.com/webhook",
        status_code=status_code,
        body="",
        headers={
            "Content-Type": "application/json",
        },
    )


class AttendanceSlackManagerTestCase(IsStaffModelAdminTestCaseBase):
    """Test case for ProjectSlackManager."""

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

    def test_attendanceslackmanager_missing_settings(self):
        """Test that the AttendanceSlackManager raises a ValueError if required settings are missing."""
        # Create an organization without required settings
        organization = self.organization
        organization.slack_api_token = ""
        organization.slack_signing_secret = ""
        organization.slack_command_name = ""
        organization.slack_attendance_report_channel = ""
        organization.save()

        for required_field in AttendanceSlackManager.REQUIRED_ORGANIZATION_FIELDS:
            original_value = getattr(organization, required_field)
            setattr(organization, required_field, "")
            with self.assertRaises(ValueError) as context:
                AttendanceSlackManager(organization=organization)
                self.assertIn("Organization is missing required field", str(context.exception))
            setattr(organization, required_field, original_value)  # re-set the original value

    @mock.patch("accounts.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.checkin.WebhookClient.send", return_value=webhook_response_factory())
    def test_valid_checkincommand(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        valid_command = "/kippo"
        for count, alias in enumerate(CheckInSubCommand.ALIASES, 1):
            payload = {
                "command": valid_command,
                "text": f"{alias} 出勤しますよ",
                "user_id": self.staffuser_with_org_slack_id,
                "response_url": "https://example.com/response_url",
            }

            manager = AttendanceSlackManager(
                organization=self.organization,
            )
            blocks, *_ = manager.process_command(payload)
            self.assertTrue(blocks)

            expected_slackcommand_count = count
            self.assertEqual(SlackCommand.objects.count(), expected_slackcommand_count)

            expected_attendancerecord_count = count
            self.assertEqual(AttendanceRecord.objects.count(), expected_attendancerecord_count)

    @mock.patch("accounts.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.BAD_REQUEST))
    @mock.patch("accounts.slackcommand.subcommands.checkin.WebhookClient.send", return_value=webhook_response_factory())
    def test_invalid_request_command_name(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        request_command_name = "/invalid"
        payload = {
            "command": request_command_name,
            "text": "XXX 出勤しますよ",
            "user_id": self.staffuser_with_org_slack_id,
            "response_url": "https://example.com/response_url",
        }

        manager = AttendanceSlackManager(
            organization=self.organization,
        )
        blocks, send_response, error_response = manager.process_command(payload)
        self.assertFalse(blocks)
        self.assertIsNone(send_response)
        self.assertTrue(error_response)
        self.assertEqual(error_response.status_code, HTTPStatus.BAD_REQUEST)

    @mock.patch("accounts.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.BAD_REQUEST))
    @mock.patch("accounts.slackcommand.subcommands.checkin.WebhookClient.send", return_value=webhook_response_factory())
    def test_valid_request_command_name__empty_subcommand(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        valid_command = "/kippo"
        empty_subcommand = ""
        payload = {
            "command": valid_command,
            "text": empty_subcommand,
            "user_id": self.staffuser_with_org_slack_id,
            "response_url": "https://example.com/response_url",
        }

        manager = AttendanceSlackManager(
            organization=self.organization,
        )
        blocks, send_response, error_response = manager.process_command(payload)
        self.assertFalse(blocks)
        self.assertIsNone(send_response)
        self.assertTrue(error_response)
        self.assertEqual(error_response.status_code, HTTPStatus.BAD_REQUEST)

    @mock.patch("accounts.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.BAD_REQUEST))
    @mock.patch("accounts.slackcommand.subcommands.checkin.WebhookClient.send", return_value=webhook_response_factory())
    def test_valid_request_command_name__invalid_subcommand_alias(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        valid_command = "/kippo"
        invalid_subcommand_alias = "invalid-subcommand other text"
        payload = {
            "command": valid_command,
            "text": invalid_subcommand_alias,
            "user_id": self.staffuser_with_org_slack_id,
            "response_url": "https://example.com/response_url",
        }

        manager = AttendanceSlackManager(
            organization=self.organization,
        )
        blocks, send_response, error_response = manager.process_command(payload)
        self.assertFalse(blocks)
        self.assertIsNone(send_response)
        self.assertTrue(error_response)
        self.assertEqual(error_response.status_code, HTTPStatus.BAD_REQUEST)
