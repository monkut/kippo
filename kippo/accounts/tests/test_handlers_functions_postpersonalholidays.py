import datetime
from http import HTTPStatus
from unittest import mock

from commons.tests import IsStaffModelAdminTestCaseBase
from commons.tests.utils import mock_slack_response_factory
from django.utils import timezone

from accounts.handlers.functions import post_personalholidays
from accounts.models import OrganizationMembership, PersonalHoliday

SLACK_RESPONSE_IMAGE_URL = "https://example.com/image.jpg"


class PostPersonalHolidaysTestCase(IsStaffModelAdminTestCaseBase):
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

        PersonalHoliday.objects.all().delete()

    @mock.patch("accounts.handlers.functions.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK))
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_postpersonalholidays__without_holidays(self, *_):
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        user_persionalholidays, personalholidays_report_blocks = post_personalholidays(event={}, context={})
        self.assertFalse(user_persionalholidays)
        self.assertFalse(personalholidays_report_blocks)

    @mock.patch("accounts.handlers.functions.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK))
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_postpersonalholidays__with_holidays_on_date(self, *_):
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        # create a personal holiday for today
        PersonalHoliday.objects.create(
            user=self.staffuser_with_org,
            day=timezone.localdate(),
        )
        user_persionalholidays, personalholidays_report_blocks = post_personalholidays(event={}, context={})

        expected_userpersonalholidays_count = 1
        self.assertEqual(len(user_persionalholidays), expected_userpersonalholidays_count)
        self.assertTrue(personalholidays_report_blocks)

    @mock.patch("accounts.handlers.functions.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK))
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_postpersonalholidays__with_holidays_duration(self, *_):
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        # create a 3 day personal holiday ending today (day-2, day-1, today)
        day = timezone.localdate() - datetime.timedelta(days=2)
        PersonalHoliday.objects.create(
            user=self.staffuser_with_org,
            day=day,
            duration=3,  # 3 days duration
        )
        user_persionalholidays, personalholidays_report_blocks = post_personalholidays(event={}, context={})

        expected_userpersonalholidays_count = 1
        self.assertEqual(len(user_persionalholidays), expected_userpersonalholidays_count)
        self.assertTrue(personalholidays_report_blocks)

    @mock.patch("accounts.handlers.functions.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK))
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_postpersonalholidays__holiday_ended_yesterday(self, *_):
        # a 1 day holiday yesterday does not cover today
        PersonalHoliday.objects.create(
            user=self.staffuser_with_org,
            day=timezone.localdate() - datetime.timedelta(days=1),
            duration=1,
        )
        user_persionalholidays, personalholidays_report_blocks = post_personalholidays(event={}, context={})
        self.assertFalse(user_persionalholidays)
        self.assertFalse(personalholidays_report_blocks)

    @mock.patch("accounts.handlers.functions.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK))
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_postpersonalholidays__consecutive_single_day_holidays(self, *_):
        # consecutive 1 day holidays (yesterday, today) report the user once
        today = timezone.localdate()
        PersonalHoliday.objects.create(user=self.staffuser_with_org, day=today - datetime.timedelta(days=1), duration=1)
        PersonalHoliday.objects.create(user=self.staffuser_with_org, day=today, duration=1)
        user_persionalholidays, personalholidays_report_blocks = post_personalholidays(event={}, context={})

        expected_userpersonalholidays_count = 1
        self.assertEqual(len(user_persionalholidays), expected_userpersonalholidays_count)
        self.assertEqual(user_persionalholidays[0]["personal_holiday"]["day"], today.isoformat())
        expected_block_count = 2  # header + 1 user
        self.assertEqual(len(personalholidays_report_blocks), expected_block_count)

    @mock.patch("accounts.handlers.functions.WebClient.chat_postMessage", return_value=mock_slack_response_factory(status_code=HTTPStatus.OK))
    @mock.patch(
        "commons.slackcommand.base.WebClient.users_info",
        return_value={"user": {"profile": {"image_192": SLACK_RESPONSE_IMAGE_URL}}},
    )
    def test_postpersonalholidays__overlapping_holidays_full_day_precedence(self, *_):
        # overlapping half-day and full-day entries report the user once as full-day
        today = timezone.localdate()
        PersonalHoliday.objects.create(user=self.staffuser_with_org, day=today, duration=1, is_half=True)
        PersonalHoliday.objects.create(user=self.staffuser_with_org, day=today - datetime.timedelta(days=1), duration=2)
        user_persionalholidays, personalholidays_report_blocks = post_personalholidays(event={}, context={})

        expected_userpersonalholidays_count = 1
        self.assertEqual(len(user_persionalholidays), expected_userpersonalholidays_count)
        self.assertFalse(user_persionalholidays[0]["personal_holiday"]["is_half"])
        self.assertIn("全休", personalholidays_report_blocks[1]["elements"][1]["text"])
