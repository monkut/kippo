import datetime
from http import HTTPStatus
from unittest import mock

from commons.tests import IsStaffModelAdminTestCaseBase
from commons.tests.utils import mock_slack_response_factory, webhook_response_factory
from django.utils import timezone

from accounts.models import OrganizationMembership, PersonalHoliday, SlackCommand
from accounts.slackcommand.subcommands.setholiday import SetHolidaySubCommand


class SetHolidaySubCommandTestCase(IsStaffModelAdminTestCaseBase):
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

        PersonalHoliday.objects.all().delete()

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.setholiday.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.setholiday.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": "https://example.com/image_192.png"}}},
    )
    def test_personalholiday_not_created_on_missing_date(self, *_):
        """Confirm that a New PersonalHoliday is not created when the date is missing from the subcommand."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="setholiday",
            text="setholiday none",
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = SetHolidaySubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)
        expected_end_personalholiday_count = 0
        actual_end_attendancerecord_count = PersonalHoliday.objects.count()
        self.assertEqual(actual_end_attendancerecord_count, expected_end_personalholiday_count)

    def test_already_set__same_registered_day(self):
        """Confirm that a *new* PersonalHoliday is not created when the date is already registered."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        # create a new PersonalHoliday
        existing_personalholiday_date = datetime.date(2025, 10, 1)
        existing_personalholiday = PersonalHoliday(
            #     user = models.ForeignKey(KippoUser, on_delete=models.CASCADE, editable=True)
            #     created_datetime = models.DateTimeField(editable=False, auto_now_add=True)
            #     is_half = models.BooleanField(default=False, help_text=_("Select if taking only a half day"))
            #     day = models.DateField()
            #     duration = models.SmallIntegerField(default=1, help_text=_("How many days (including weekends/existing holidays)"))
            user=self.staffuser_with_org,
            day=existing_personalholiday_date,
            duration=1,
        )
        existing_personalholiday.save()

        valid_subcommand_text = f"setholiday {existing_personalholiday_date.strftime('%Y-%m-%d')}"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="setholiday",
            text=valid_subcommand_text,
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = SetHolidaySubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)
        expected_personalholiday_count = 1  # Only the existing one, created above, should be present
        actual_personalholiday_count = PersonalHoliday.objects.count()
        self.assertEqual(actual_personalholiday_count, expected_personalholiday_count)

    def test_already_set__within_registered_duration(self):
        """Confirm that a *new* PersonalHoliday is not created when the date is already registered in the duration."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        # create a new PersonalHoliday
        existing_personalholiday_date = datetime.date(2025, 10, 1)
        existing_personalholiday = PersonalHoliday(
            #     user = models.ForeignKey(KippoUser, on_delete=models.CASCADE, editable=True)
            #     created_datetime = models.DateTimeField(editable=False, auto_now_add=True)
            #     is_half = models.BooleanField(default=False, help_text=_("Select if taking only a half day"))
            #     day = models.DateField()
            #     duration = models.SmallIntegerField(default=1, help_text=_("How many days (including weekends/existing holidays)"))
            user=self.staffuser_with_org,
            day=existing_personalholiday_date,
            duration=2,  # Duration of 2 days
        )
        existing_personalholiday.save()

        command_date = existing_personalholiday_date + datetime.timedelta(days=1)
        valid_subcommand_text = f"setholiday {command_date.strftime('%Y-%m-%d')}"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="setholiday",
            text=valid_subcommand_text,
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = SetHolidaySubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertFalse(web_response)
        self.assertTrue(webhook_response)
        expected_personalholiday_count = 1  # Only the existing one, created above, should be present
        actual_personalholiday_count = PersonalHoliday.objects.count()
        self.assertEqual(actual_personalholiday_count, expected_personalholiday_count)

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.setholiday.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.setholiday.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": "https://example.com/image_192.png"}}},
    )
    def test_valid_fullday_date(self, *_):
        """Confirm that a *new* PersonalHoliday is *created* when a valid date is given."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        command_date = (timezone.now() + timezone.timedelta(days=1)).date()
        valid_subcommand_text = f"setholiday {command_date.strftime('%Y-%m-%d')}"
        command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="setholiday",
            text=valid_subcommand_text,
            response_url="https://example.com/response_url",
        )
        command.save()

        blocks, web_response, webhook_response = SetHolidaySubCommand.handle(command)
        self.assertTrue(blocks)
        self.assertTrue(web_response)
        self.assertTrue(webhook_response)
        expected_personalholiday_count = 1
        actual_personalholiday_count = PersonalHoliday.objects.count()
        self.assertEqual(actual_personalholiday_count, expected_personalholiday_count)

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.setholiday.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.setholiday.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": "https://example.com/image_192.png"}}},
    )
    def test_valid_halfday_date(self, *_):
        """Confirm that a *new* PersonalHoliday (half-day) is *created* when a valid date is given."""
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        command_date = (timezone.now() + timezone.timedelta(days=1)).date()

        for halfday_alias in SetHolidaySubCommand.HALF_DAY_SUBCOMMANDS:
            valid_subcommand_text = f"{halfday_alias} {command_date.strftime('%Y-%m-%d')}"
            command = SlackCommand(
                organization=self.organization,
                user=self.staffuser_with_org,
                sub_command=halfday_alias,
                text=valid_subcommand_text,
                response_url="https://example.com/response_url",
            )
            command.save()

            blocks, web_response, webhook_response = SetHolidaySubCommand.handle(command)
            self.assertTrue(blocks)
            self.assertTrue(web_response)
            self.assertTrue(webhook_response)
            expected_personalholiday_count = 1
            actual_personalholiday_count = PersonalHoliday.objects.count()
            self.assertEqual(actual_personalholiday_count, expected_personalholiday_count)

            personalholiday = PersonalHoliday.objects.first()
            self.assertTrue(personalholiday.is_half)

            personalholiday.delete()  # delete for next loop

    def test_subcommand_registered(self):
        """Confirm that the subcommand is registered."""
        from commons.slackcommand import get_all_subcommands

        available_subcommands = get_all_subcommands()
        self.assertIn(SetHolidaySubCommand, available_subcommands)
