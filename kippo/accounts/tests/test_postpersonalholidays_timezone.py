import datetime
from http import HTTPStatus
from unittest import mock

from commons.tests import IsStaffModelAdminTestCaseBase
from commons.tests.utils import mock_slack_response_factory
from django.utils import timezone
from freezegun import freeze_time

from accounts.handlers.functions import post_personalholidays
from accounts.models import OrganizationMembership, PersonalHoliday

SLACK_RESPONSE_IMAGE_URL = "https://example.com/image.jpg"


class PostPersonalHolidaysTimezoneTestCase(IsStaffModelAdminTestCaseBase):
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
    def test_postpersonalholidays_timezone_handling_regression(self, *_):
        """
        Test that post_personalholidays() correctly handles timezone conversion when the system
        runs in UTC but the organization is in JST (+9).

        This test ensures that PersonalHoliday dates are properly matched against the current
        date in the organization's timezone (JST), not the system timezone (UTC).

        The test runs post_personalholidays() at three different UTC times:
        1. At 15:00 UTC (00:00 JST next day) - should find the holiday
        2. At 14:59 UTC (23:59 JST same day) - should not find the holiday
        3. At next day 15:00 UTC - should not find the holiday (day after)
        """
        expected_personalholiday_count = 0
        assert PersonalHoliday.objects.count() == expected_personalholiday_count

        # Create a PersonalHoliday for July 25, 2025 (JST date)
        holiday_date_jst = datetime.date(2025, 7, 25)
        PersonalHoliday.objects.create(
            user=self.staffuser_with_org,
            day=holiday_date_jst,
            duration=1,
        )

        # Test 1: Current time is 2025-07-25 00:00:00 JST (2025-07-24 15:00:00 UTC)
        # At this time, it should be July 25 in JST timezone, so the holiday should be found
        utc_datetime_on_target_date = datetime.datetime(2025, 7, 24, 15, 0, 0, tzinfo=datetime.UTC)

        with freeze_time(utc_datetime_on_target_date):
            # Verify the current time setup
            current_utc = timezone.now()
            current_jst = timezone.localtime()  # Should be JST due to settings.TIME_ZONE = "Asia/Tokyo"

            self.assertEqual(current_utc.date(), datetime.date(2025, 7, 24))  # UTC date
            self.assertEqual(current_jst.date(), datetime.date(2025, 7, 25))  # JST date (next day)

            # Run post_personalholidays - should find the holiday because it's July 25 in JST
            user_persionalholidays, personalholidays_report_blocks = post_personalholidays(event={}, context={})

            # Verify holiday was found
            expected_userpersonalholidays_count = 1
            self.assertEqual(len(user_persionalholidays), expected_userpersonalholidays_count)
            self.assertTrue(personalholidays_report_blocks)

            # Verify correct holiday data
            found_holiday = user_persionalholidays[0]
            self.assertEqual(found_holiday["user"], self.staffuser_with_org.username)
            self.assertEqual(found_holiday["personal_holiday"]["day"], "2025-07-25")

        # Test 2: Current time is 2025-07-24 23:59:00 JST (2025-07-24 14:59:00 UTC)
        # At this time, it should still be July 24 in JST timezone, so the holiday should NOT be found
        utc_datetime_before_target_date = datetime.datetime(2025, 7, 24, 14, 59, 0, tzinfo=datetime.UTC)

        with freeze_time(utc_datetime_before_target_date):
            # Verify the current time setup
            current_utc = timezone.now()
            current_jst = timezone.localtime()  # Should be JST due to settings.TIME_ZONE = "Asia/Tokyo"

            self.assertEqual(current_utc.date(), datetime.date(2025, 7, 24))  # UTC date
            self.assertEqual(current_jst.date(), datetime.date(2025, 7, 24))  # JST date (same day)

            # Run post_personalholidays - should NOT find the holiday because it's still July 24 in JST
            user_persionalholidays, personalholidays_report_blocks = post_personalholidays(event={}, context={})

            # Verify holiday was NOT found
            expected_userpersonalholidays_count = 0
            self.assertEqual(len(user_persionalholidays), expected_userpersonalholidays_count)
            self.assertFalse(personalholidays_report_blocks)

        # Additional verification: Test boundary case at exactly JST midnight
        # This ensures the timezone handling is precise at the day boundary
        jst_midnight_utc = datetime.datetime(2025, 7, 24, 15, 0, 0, tzinfo=datetime.UTC)

        with freeze_time(jst_midnight_utc):
            current_jst = timezone.localtime()
            # This should be exactly 2025-07-25 00:00:00 JST
            self.assertEqual(current_jst.hour, 0)
            self.assertEqual(current_jst.minute, 0)
            self.assertEqual(current_jst.date(), datetime.date(2025, 7, 25))

            # The function should properly find the July 25 holiday at JST midnight
            user_persionalholidays, _ = post_personalholidays(event={}, context={})
            self.assertEqual(len(user_persionalholidays), 1)
