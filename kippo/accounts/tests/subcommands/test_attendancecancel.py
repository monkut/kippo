import datetime
from unittest import mock

from commons.tests import IsStaffModelAdminTestCaseBase
from commons.tests.utils import webhook_response_factory
from django.conf import settings
from django.utils import timezone

from accounts.definitions import AttendanceRecordCategory
from accounts.models import AttendanceRecord, OrganizationMembership, PersonalHoliday, SlackCommand
from accounts.slackcommand.subcommands.attendancecancel import AttendanceCancelSubCommand

SLACK_RESPONSE_IMAGE_URL = "https://example.com/image.jpg"


class AttendanceCancelSubCommandTestCase(IsStaffModelAdminTestCaseBase):
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

        self.membership = OrganizationMembership.objects.get(organization=self.organization, user=self.staffuser_with_org)
        self.membership.slack_user_id = self.staffuser_with_org_slack_id
        self.membership.slack_image_url = ""
        self.membership.slack_username = self.staffuser_with_org_slack_username
        self.membership.save()

        PersonalHoliday.objects.all().delete()

    @mock.patch("accounts.slackcommand.subcommands.attendancecancel.WebhookClient.send", return_value=webhook_response_factory())
    def test_no_attendencerecords(self, *_):
        """Confirm the expected response returned when no attendance records are found."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="attendance-cancel",
            text="attendance-cancel",
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = AttendanceCancelSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_block_count = 1
        self.assertEqual(len(blocks), expected_block_count)
        response_block = blocks[0]
        self.assertIn("記録がみつかりません", response_block["text"]["text"])

    @mock.patch("accounts.slackcommand.subcommands.attendancecancel.WebhookClient.send", return_value=webhook_response_factory())
    def test_with_deletable_attendancerecord(self, *_):
        """Confirm the expected response returned when AttendanceRecord found and deleted."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        for category in AttendanceRecordCategory.values():
            # create AttendanceRecord for a user in the organization
            entry_datetime = timezone.localtime()
            initial_image_url = "https://example.com/image.jpg"
            AttendanceRecord.objects.create(
                organization=self.membership.organization,
                date=entry_datetime.date(),
                created_by=self.membership.user,
                updated_by=self.membership.user,
                category=category,
                entry_datetime=entry_datetime,
            )
            # update the user image URL
            self.membership.slack_image_url = initial_image_url
            self.membership.save()

            command = SlackCommand(
                organization=self.organization,
                user=self.staffuser_with_org,
                sub_command="attendance-cancel",
                text="attendance-cancel",
                response_url="https://example.com/response_url",
            )
            command.save()

            blocks, web_response, webhook_response = AttendanceCancelSubCommand.handle(command)
            self.assertTrue(blocks)
            self.assertFalse(web_response)
            self.assertTrue(webhook_response)

            expected_attendancerecord_count = 0
            self.assertEqual(AttendanceRecord.objects.count(), expected_attendancerecord_count)

    @mock.patch("accounts.slackcommand.subcommands.attendancecancel.WebhookClient.send", return_value=webhook_response_factory())
    def test_with_nondeletable_attendancerecord(self, *_):
        """Confirm the expected response returned when AttendanceRecord found and deleted."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        non_deleteable_entry_datetime = timezone.localtime() - datetime.timedelta(minutes=settings.ATTENDANCECANCEL_SUBCOMMAND_MINUTES + 1)
        for category in AttendanceRecordCategory.values():
            # create AttendanceRecord for a user in the organization
            entry_datetime = timezone.localtime()
            initial_image_url = "https://example.com/image.jpg"
            AttendanceRecord.objects.create(
                organization=self.membership.organization,
                date=entry_datetime.date(),
                created_by=self.membership.user,
                updated_by=self.membership.user,
                category=category,
                entry_datetime=non_deleteable_entry_datetime,
            )
            # update the user image URL
            self.membership.slack_image_url = initial_image_url
            self.membership.save()

            command = SlackCommand(
                organization=self.organization,
                user=self.staffuser_with_org,
                sub_command="attendance-cancel",
                text="attendance-cancel",
                response_url="https://example.com/response_url",
            )
            command.save()

            blocks, web_response, webhook_response = AttendanceCancelSubCommand.handle(command)
            self.assertTrue(blocks)
            self.assertFalse(web_response)
            self.assertTrue(webhook_response)

            expected_attendancerecord_count = 1  # not deleted!
            self.assertEqual(AttendanceRecord.objects.count(), expected_attendancerecord_count)

            # delete for next category test
            AttendanceRecord.objects.all().delete()
