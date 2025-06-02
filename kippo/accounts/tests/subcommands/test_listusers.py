import datetime
from http import HTTPStatus
from unittest import mock

from commons.tests import IsStaffModelAdminTestCaseBase
from commons.tests.utils import mock_slack_response_factory, webhook_response_factory
from django.conf import settings
from django.utils import timezone

from accounts.definitions import AttendanceRecordCategory
from accounts.models import AttendanceRecord, OrganizationMembership, PersonalHoliday, SlackCommand
from accounts.slackcommand.subcommands.listusers import ListUsersSubCommand

SLACK_RESPONSE_IMAGE_URL = "https://example.com/image.jpg"


class ListUsersSubCommandTestCase(IsStaffModelAdminTestCaseBase):
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

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.listusers.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.listusers.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "accounts.slackcommand.subcommands.listusers.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_no_attendencerecords(self, *_):
        """Confirm the expected response returned when no attendance records are found."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="list-users",
            text="list-users",
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = ListUsersSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        expected_block_count = 1
        self.assertEqual(len(blocks), expected_block_count)
        response_block = blocks[0]
        self.assertIn("出勤記録がみつかりません", response_block["text"]["text"])

    @mock.patch("accounts.slackcommand.subcommands.listusers.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.listusers.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "accounts.slackcommand.subcommands.listusers.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_attendancerecord_user_with_valid_image_url(self, *_):
        """Confirm the expected response returned when AttendanceRecord found with valid image URLs."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        # create AttendanceRecord for a user in the organization
        entry_datetime = timezone.localtime()
        initial_image_url = "https://example.com/image.jpg"
        AttendanceRecord.objects.create(
            organization=self.membership.organization,
            date=entry_datetime.date(),
            created_by=self.membership.user,
            updated_by=self.membership.user,
            category=AttendanceRecordCategory.START,
            entry_datetime=entry_datetime,
        )
        # update the user image URL
        self.membership.slack_image_url = initial_image_url
        self.membership.save()

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="list-users",
            text="list-users",
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = ListUsersSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        # 1 section header "section" block and 1 user "context" block
        expected_block_count = 2
        self.assertEqual(len(blocks), expected_block_count)
        response_block_header = blocks[0]
        response_block_user = blocks[1]

        self.assertEqual(response_block_header["type"], "section")
        self.assertEqual(response_block_user["type"], "context")

    @mock.patch("accounts.slackcommand.subcommands.listusers.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.listusers.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "accounts.slackcommand.subcommands.listusers.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_attendancerecord_user_without_image_url(self, *_):
        """Confirm user_image_url is retrieved and membership is populated with a user without image URL."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        # create AttendanceRecord for a user in the organization
        entry_datetime = timezone.localtime()
        AttendanceRecord.objects.create(
            organization=self.membership.organization,
            date=entry_datetime.date(),
            created_by=self.membership.user,
            updated_by=self.membership.user,
            category=AttendanceRecordCategory.START,
            entry_datetime=entry_datetime,
        )
        # update the user image URL
        assert not self.membership.slack_image_url

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="list-users",
            text="list-users",
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = ListUsersSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        # 1 section header "section" block and 1 user "context" block
        expected_block_count = 2
        self.assertEqual(len(blocks), expected_block_count)
        response_block_header = blocks[0]
        response_block_user = blocks[1]

        self.assertEqual(response_block_header["type"], "section")
        self.assertEqual(response_block_user["type"], "context")

        self.membership.refresh_from_db()
        self.assertTrue(self.membership.slack_image_url)

    @mock.patch("accounts.slackcommand.subcommands.listusers.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.listusers.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "accounts.slackcommand.subcommands.listusers.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_attendancerecord_user_with_invalid_image_url(self, *_):
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_attendancerecord_count = 0
        assert AttendanceRecord.objects.count() == expected_attendancerecord_count

        # create AttendanceRecord for a user in the organization
        entry_datetime = timezone.localtime()
        initial_image_url = "https://example.com/image-initial.jpg"
        AttendanceRecord.objects.create(
            organization=self.membership.organization,
            date=entry_datetime.date(),
            created_by=self.membership.user,
            updated_by=self.membership.user,
            category=AttendanceRecordCategory.START,
            entry_datetime=entry_datetime,
        )
        # update the user image URL
        self.membership.slack_image_url = initial_image_url
        self.membership.save()

        # -- set updated_datetime to Old value to simulate an invalid image URL
        days = settings.REFRESH_SLACK_IMAGE_URL_DAYS + 1  # add one day to ensure the image URL is considered invalid
        # perform update to by-pass auto_now date update
        new_datetime = timezone.now() - datetime.timedelta(days=days)
        OrganizationMembership.objects.filter(pk=self.membership.pk).update(updated_datetime=new_datetime)

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="list-users",
            text="list-users",
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = ListUsersSubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)

        # 1 section header "section" block and 1 user "context" block
        expected_block_count = 2
        self.assertEqual(len(blocks), expected_block_count)
        response_block_header = blocks[0]
        response_block_user = blocks[1]

        self.assertEqual(response_block_header["type"], "section")
        self.assertEqual(response_block_user["type"], "context")

        self.membership.refresh_from_db()
        self.assertTrue(self.membership.slack_image_url)
        self.assertEqual(self.membership.slack_image_url, SLACK_RESPONSE_IMAGE_URL)
        today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertGreaterEqual(self.membership.updated_datetime, today)

    def test_subcommand_registered(self):
        """Confirm that the subcommand is registered."""
        from commons.slackcommand import get_all_subcommands

        available_subcommands = get_all_subcommands()
        self.assertIn(ListUsersSubCommand, available_subcommands)
