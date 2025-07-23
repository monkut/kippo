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

    @mock.patch("commons.slackcommand.managers.WebhookClient.send", return_value=webhook_response_factory(status_code=HTTPStatus.OK))
    @mock.patch("accounts.slackcommand.subcommands.setholiday.WebhookClient.send", return_value=webhook_response_factory())
    @mock.patch(
        "accounts.slackcommand.subcommands.setholiday.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK)
    )
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": "https://example.com/image_192.png"}}},
    )
    def test_consecutive_dates_duration_bug_regression(self, *_):
        """
        Reproduce the bug where '/kippo setholiday 25/07/24' followed by '/kippo setholiday 25/07/25'
        incorrectly failed with 'PersonalHoliday already exists'.

        This was caused by incorrect duration handling where duration=1 blocked 2 days instead of 1.
        """
        expected_slackcommand_count = 0
        assert SlackCommand.objects.count() == expected_slackcommand_count
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        # First command: Set holiday for July 24, 2025
        first_date = datetime.date(2025, 7, 24)
        first_command_text = f"setholiday {first_date.strftime('%Y/%m/%d')}"
        first_command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="setholiday",
            text=first_command_text,
            response_url="https://example.com/response_url",
        )
        first_command.save()

        # Execute first command - should succeed
        blocks1, web_response1, webhook_response1 = SetHolidaySubCommand.handle(first_command)
        self.assertTrue(blocks1)
        self.assertTrue(web_response1)  # Should create new PersonalHoliday
        self.assertTrue(webhook_response1)

        # Verify first PersonalHoliday was created
        expected_personalholiday_count = 1
        actual_personalholiday_count = PersonalHoliday.objects.count()
        self.assertEqual(actual_personalholiday_count, expected_personalholiday_count)

        first_holiday = PersonalHoliday.objects.first()
        self.assertEqual(first_holiday.day, first_date)
        self.assertEqual(first_holiday.duration, 1)
        self.assertFalse(first_holiday.is_half)

        # Second command: Set holiday for July 25, 2025 (next day)
        second_date = datetime.date(2025, 7, 25)
        second_command_text = f"setholiday {second_date.strftime('%Y/%m/%d')}"
        second_command = SlackCommand(
            organization=self.organization,
            user=self.staffuser_with_org,
            sub_command="setholiday",
            text=second_command_text,
            response_url="https://example.com/response_url",
        )
        second_command.save()

        # Execute second command - should succeed (this would fail before the fix)
        blocks2, web_response2, webhook_response2 = SetHolidaySubCommand.handle(second_command)
        self.assertTrue(blocks2)
        self.assertTrue(web_response2)  # Should create new PersonalHoliday
        self.assertTrue(webhook_response2)

        # Verify second PersonalHoliday was created
        expected_personalholiday_count = 2
        actual_personalholiday_count = PersonalHoliday.objects.count()
        self.assertEqual(actual_personalholiday_count, expected_personalholiday_count)

        # Verify we have holidays for both dates
        all_holidays = PersonalHoliday.objects.order_by("day")
        self.assertEqual(len(all_holidays), 2)
        self.assertEqual(all_holidays[0].day, first_date)
        self.assertEqual(all_holidays[1].day, second_date)
        self.assertEqual(all_holidays[0].duration, 1)
        self.assertEqual(all_holidays[1].duration, 1)

        # Verify error message is NOT present (the bug would show "休みがすでに（2025-07-25）に登録されています。")
        error_block = None
        for block in blocks2:
            if block.get("type") == "section" and "休みがすでに" in block.get("text", {}).get("text", ""):
                error_block = block
                break
        self.assertIsNone(error_block, "Second setholiday command should not show 'already registered' error")

    def test_subcommand_registered(self):
        """Confirm that the subcommand is registered."""
        from commons.slackcommand import get_all_subcommands

        available_subcommands = get_all_subcommands()
        self.assertIn(SetHolidaySubCommand, available_subcommands)
