from http import HTTPStatus
from unittest import mock

from commons.slackcommand.managers import SlackCommandManager
from commons.tests import IsStaffModelAdminTestCaseBase
from commons.tests.utils import mock_slack_response_factory, webhook_response_factory
from django.conf import settings
from django.utils import timezone

from accounts.definitions import AttendanceRecordCategory
from accounts.models import AttendanceRecord, OrganizationMembership, SlackCommand
from accounts.slackcommand.subcommands.clockout import ClockOutSubCommand


class ClockOutSubCommandTestCase(IsStaffModelAdminTestCaseBase):
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

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.clockout.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": "https://example.com/image_192.png"}}},
    )
    def test_attendancerecord_not_created_on_missing_start(self, *_):
        """Confirm that END AttendanceRecord is not created if there is no START record."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="clockout",
            text="clockout 退勤しますよ",
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = ClockOutSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)
        expected_end_attendancerecord_count = 0
        actual_end_attendancerecord_count = AttendanceRecord.objects.filter(category=AttendanceRecordCategory.END).count()
        self.assertEqual(actual_end_attendancerecord_count, expected_end_attendancerecord_count)

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.clockout.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": "https://example.com/image_192.png"}}},
    )
    def test_attendanceslackmanager_processcommand_clockoutcommand_aliases(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        # create the initial START record to allow valid clockout
        AttendanceRecord.objects.create(
            created_by=self.staffuser_with_org,
            updated_by=self.staffuser_with_org,
            organization=self.organization,
            category=AttendanceRecordCategory.START,
            entry_datetime=timezone.now(),
        )

        valid_command = "/kippo"
        slackcommand_expected_count = 1
        for alias in ClockOutSubCommand.ALIASES:
            payload = {
                "command": valid_command,
                "text": f"{alias} 退勤しますよ",
                "user_id": self.staffuser_with_org_slack_id,
                "user_name": self.staffuser_with_org_slack_username,
                "response_url": "https://example.com/response_url",
            }

            manager = SlackCommandManager(
                organization=self.organization,
            )
            blocks, *_ = manager.process_command(payload)
            self.assertTrue(blocks)

            self.assertEqual(SlackCommand.objects.count(), slackcommand_expected_count)

            expected_attendancerecord_count = 1
            self.assertEqual(AttendanceRecord.objects.filter(category=AttendanceRecordCategory.END).count(), expected_attendancerecord_count)
            AttendanceRecord.objects.filter(category=AttendanceRecordCategory.END).delete()

            slackcommand_expected_count += 1

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.clockout.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": "https://example.com/image_192.png"}}},
    )
    def test_with_preexisting_attendancerecord(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        # create the initial START record to allow valid clockout
        AttendanceRecord.objects.create(
            created_by=self.staffuser_with_org,
            updated_by=self.staffuser_with_org,
            organization=self.organization,
            category=AttendanceRecordCategory.START,
            entry_datetime=timezone.now(),
        )

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="clockout",
            text="clockout 退勤しますよ",
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = ClockOutSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertTrue(web_response)
        self.assertTrue(webhook_response)

        # Check that a *NEW* attendance record was not created
        blocks, web_response, webhook_response = ClockOutSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)
        self.assertEqual(AttendanceRecord.objects.filter(category=AttendanceRecordCategory.END).count(), 1)

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.clockout.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": "https://example.com/image_192.png"}}},
    )
    def test_set_by_datetime(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        # create the initial START record to allow valid clockout
        start_entry_datetime = (timezone.now() - timezone.timedelta(hours=8)).replace(minute=0, second=0, microsecond=0)
        AttendanceRecord.objects.create(
            created_by=self.staffuser_with_org,
            updated_by=self.staffuser_with_org,
            organization=self.organization,
            category=AttendanceRecordCategory.START,
            entry_datetime=start_entry_datetime,
        )

        expected_entry_datetime = (timezone.now() - timezone.timedelta(hours=4)).replace(minute=0, second=0, microsecond=0).astimezone(settings.JST)
        subcommand_text = f"clockout {expected_entry_datetime.strftime('%Y-%m-%d %H:%M')}"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="clockout",
            text=subcommand_text,
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = ClockOutSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)  # Not sent when date is manually set!
        self.assertTrue(webhook_response)

        expected_end_attendancerecord_count = 1
        actual_end_attendancerecord_count = AttendanceRecord.objects.filter(category=AttendanceRecordCategory.END).count()
        self.assertEqual(actual_end_attendancerecord_count, expected_end_attendancerecord_count)

        # check datetime is as expected
        end_attendance_record = AttendanceRecord.objects.filter(category=AttendanceRecordCategory.END).first()
        self.assertEqual(end_attendance_record.entry_datetime, expected_entry_datetime)

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.clockout.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "accounts.slackcommand.subcommands.clockout.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": "https://example.com/image_192.png"}}},
    )
    def test_set_by_datetime_without_year(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        # create the initial START record to allow valid clockout
        start_entry_datetime = (timezone.now() - timezone.timedelta(hours=8)).replace(minute=0, second=0, microsecond=0)
        AttendanceRecord.objects.create(
            created_by=self.staffuser_with_org,
            updated_by=self.staffuser_with_org,
            organization=self.organization,
            category=AttendanceRecordCategory.START,
            entry_datetime=start_entry_datetime,
        )

        expected_entry_datetime = (timezone.now() - timezone.timedelta(hours=4)).replace(minute=0, second=0, microsecond=0).astimezone(settings.JST)
        for separator in ("/", "-"):
            date_format = f"%m{separator}%d %H:%M"
            subcommand_text = f"clockout {expected_entry_datetime.strftime(date_format)}"
            command = SlackCommand(
                organization=self.organization,
                user=self.staffuser_with_org,
                sub_command="clockout",
                text=subcommand_text,
                response_url="https://example.com/response_url",
            )
            command.save()

            blocks, web_response, webhook_response = ClockOutSubCommand.handle(command)
            self.assertTrue(blocks)
            self.assertFalse(web_response)  # Not sent when date is manually set!
            self.assertTrue(webhook_response)

            expected_end_attendancerecord_count = 1
            actual_end_attendancerecord_count = AttendanceRecord.objects.filter(category=AttendanceRecordCategory.END).count()
            self.assertEqual(actual_end_attendancerecord_count, expected_end_attendancerecord_count)

            # check datetime is as expected
            end_attendance_record = AttendanceRecord.objects.filter(category=AttendanceRecordCategory.END).first()
            self.assertEqual(end_attendance_record.entry_datetime, expected_entry_datetime)

            AttendanceRecord.objects.filter(category=AttendanceRecordCategory.END).delete()
