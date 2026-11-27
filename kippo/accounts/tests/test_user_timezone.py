"""KippoUser timezone and its use by the Slack attendance / holiday commands — kiconiaworks/kippo#80."""

import datetime
import zoneinfo
from unittest import mock

from commons.functions import country_timezone_names, single_timezone_name_for_country
from commons.tests import IsStaffModelAdminTestCaseBase
from commons.tests.utils import webhook_response_factory
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from freezegun import freeze_time

from accounts.definitions import AttendanceRecordCategory
from accounts.models import AttendanceRecord, Country, OrganizationMembership, SlackCommand
from accounts.slackcommand.subcommands.clockin import ClockInSubCommand
from accounts.slackcommand.subcommands.listusers import ListUsersSubCommand

TOKYO = zoneinfo.ZoneInfo("Asia/Tokyo")
LONDON = zoneinfo.ZoneInfo("Europe/London")
LOS_ANGELES = zoneinfo.ZoneInfo("America/Los_Angeles")

# Sunday 2026-03-01 18:00 in Los Angeles (PST, UTC-8) == Monday 2026-03-02 11:00 JST
SUNDAY_EVENING_LOS_ANGELES = "2026-03-02 02:00:00"
LOS_ANGELES_TODAY = datetime.date(2026, 3, 1)
# 2025-12-31 18:00 in Los Angeles == 2026-01-01 11:00 JST
NEW_YEARS_EVE_LOS_ANGELES = "2026-01-01 02:00:00"


class SingleTimezoneNameForCountryTestCase(SimpleTestCase):
    def test_country_with_one_timezone(self):
        self.assertEqual(single_timezone_name_for_country("JP"), "Asia/Tokyo")

    def test_country_code_is_case_insensitive(self):
        self.assertEqual(single_timezone_name_for_country("jp"), "Asia/Tokyo")

    def test_country_with_several_timezones_gives_none(self):
        self.assertGreater(len(country_timezone_names()["US"]), 1)
        self.assertIsNone(single_timezone_name_for_country("US"))

    def test_unknown_country_gives_none(self):
        self.assertIsNone(single_timezone_name_for_country("ZZ"))

    def test_missing_zone_table_derives_nothing(self):
        country_timezone_names.cache_clear()
        self.addCleanup(country_timezone_names.cache_clear)
        with mock.patch("commons.functions._read_zone_table", return_value=""):
            self.assertIsNone(single_timezone_name_for_country("JP"))


class UserTimezoneTestCaseBase(IsStaffModelAdminTestCaseBase):
    def setUp(self):
        super().setUp()
        self.user = self.staffuser_with_org
        self.japan = Country.objects.create(name="Japan", alpha_2="JP", alpha_3="JPN", country_code="392", region="Asia")
        self.united_states = Country.objects.create(name="United States", alpha_2="US", alpha_3="USA", country_code="840", region="Americas")


class KippoUserGetTimezoneTestCase(UserTimezoneTestCaseBase):
    def setUp(self):
        super().setUp()
        self.organization.timezone_name = "Europe/London"
        self.organization.save()

    def test_own_timezone_name_wins(self):
        self.user.timezone_name = "America/Los_Angeles"
        self.user.holiday_country = self.japan
        self.assertEqual(self.user.get_timezone(self.organization), LOS_ANGELES)

    def test_blank_derives_from_single_timezone_holiday_country(self):
        self.user.holiday_country = self.japan
        self.assertEqual(self.user.get_timezone(self.organization), TOKYO)

    def test_blank_with_multi_timezone_country_uses_the_organization(self):
        self.user.holiday_country = self.united_states
        self.assertEqual(self.user.get_timezone(self.organization), LONDON)

    def test_blank_without_country_uses_the_organization(self):
        self.assertEqual(self.user.get_timezone(self.organization), LONDON)

    def test_blank_without_country_or_organization_is_jst(self):
        self.assertEqual(self.user.get_timezone(), TOKYO)

    def test_invalid_stored_name_falls_through(self):
        self.user.timezone_name = "Not/AZone"
        self.user.holiday_country = self.japan
        self.assertEqual(self.user.get_timezone(self.organization), TOKYO)

    def test_field_validation(self):
        field = self.user._meta.get_field("timezone_name")
        self.assertEqual(field.clean("", self.user), "")
        self.assertEqual(field.clean("America/Los_Angeles", self.user), "America/Los_Angeles")
        with self.assertRaises(ValidationError):
            field.clean("Not/AZone", self.user)

    def test_attendance_record_date_is_the_date_where_the_member_works(self):
        self.user.timezone_name = "America/Los_Angeles"
        self.user.save()
        # 2026-03-02 02:00 UTC, as read back from the database: still 03-01 in Los Angeles
        entry_datetime = datetime.datetime(2026, 3, 2, 2, 0, tzinfo=datetime.UTC)
        record = AttendanceRecord(organization=self.organization, created_by=self.user, entry_datetime=entry_datetime)
        record.clean()
        self.assertEqual(record.date, LOS_ANGELES_TODAY)


@mock.patch("accounts.slackcommand.subcommands.clockin.WebhookClient.send", return_value=webhook_response_factory())
@mock.patch("accounts.slackcommand.subcommands.listusers.WebhookClient.send", return_value=webhook_response_factory())
@mock.patch("commons.slackcommand.base.WebClient.users_info", return_value={"user": {"profile": {"image_192": "https://example.com/image.png"}}})
class SlackCommandMemberTimezoneTestCase(UserTimezoneTestCaseBase):
    """A member in Los Angeles runs the commands; the server timezone is JST."""

    def setUp(self):
        super().setUp()
        self.organization.slack_api_token = "xoxb-test"  # noqa: S105
        self.organization.slack_command_name = "kippo"
        self.organization.slack_attendance_report_channel = "#kippo"
        self.organization.save()
        self.user.timezone_name = "America/Los_Angeles"
        self.user.save()
        membership = OrganizationMembership.objects.get(organization=self.organization, user=self.user)
        membership.slack_user_id = "U12345678"
        membership.slack_image_url = "https://example.com/image.png"
        membership.save()

    def _command(self, sub_command: str, text: str) -> SlackCommand:
        return SlackCommand.objects.create(
            organization=self.organization, user=self.user, sub_command=sub_command, text=text, response_url="https://example.com/response_url"
        )

    @freeze_time(SUNDAY_EVENING_LOS_ANGELES)
    def test_typed_time_is_read_in_the_members_timezone(self, *_):
        ClockInSubCommand.handle(self._command("clockin", "clockin 03/01 09:00"))
        record = AttendanceRecord.objects.get(category=AttendanceRecordCategory.START)
        self.assertEqual(record.entry_datetime, datetime.datetime(2026, 3, 1, 9, 0, tzinfo=LOS_ANGELES))
        self.assertEqual(record.entry_datetime, datetime.datetime(2026, 3, 1, 17, 0, tzinfo=datetime.UTC))

    @freeze_time(NEW_YEARS_EVE_LOS_ANGELES)
    def test_missing_year_is_the_current_year_where_the_member_is(self, *_):
        ClockInSubCommand.handle(self._command("clockin", "clockin 12/31 09:00"))
        record = AttendanceRecord.objects.get(category=AttendanceRecordCategory.START)
        self.assertEqual(record.entry_datetime, datetime.datetime(2025, 12, 31, 9, 0, tzinfo=LOS_ANGELES))

    @freeze_time(SUNDAY_EVENING_LOS_ANGELES)
    def test_list_users_today_is_the_members_today(self, *_):
        # 01:00 this morning in Los Angeles. In JST that is yesterday evening (03-01 18:00, today being 03-02).
        entry_datetime = datetime.datetime(2026, 3, 1, 1, 0, tzinfo=LOS_ANGELES)
        AttendanceRecord.objects.create(
            organization=self.organization,
            created_by=self.user,
            updated_by=self.user,
            category=AttendanceRecordCategory.START,
            entry_datetime=entry_datetime,
        )
        blocks, *_ = ListUsersSubCommand.handle(self._command("listusers", "listusers"))
        rendered = str(blocks)
        self.assertIn("今日（2026-03-01）の出勤記録", rendered)
        self.assertIn("3/1 1:00", rendered)
