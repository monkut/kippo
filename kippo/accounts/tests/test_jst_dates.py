"""'Today' is the JST date, not the UTC date (see projects/tests/test_jst_dates.py)."""

import datetime

from commons.functions import local_date
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from freezegun import freeze_time

from accounts.models import AttendanceRecord, KippoUser, OrganizationInvite

# Monday 2026-03-02 08:30 JST == Sunday 2026-03-01 23:30 UTC
MONDAY_MORNING_JST = "2026-03-01 23:30:00"
JST_TODAY = datetime.date(2026, 3, 2)
UTC_MOMENT = datetime.datetime(2026, 3, 1, 23, 30, tzinfo=datetime.UTC)


class LocalDateTestCase(SimpleTestCase):
    def test_aware_utc_datetime_gives_the_jst_date(self):
        self.assertEqual(UTC_MOMENT.date(), datetime.date(2026, 3, 1))
        self.assertEqual(local_date(UTC_MOMENT), JST_TODAY)

    def test_aware_jst_datetime_keeps_its_date(self):
        self.assertEqual(local_date(UTC_MOMENT.astimezone(settings.JST)), JST_TODAY)

    def test_naive_datetime_is_taken_as_local(self):
        self.assertEqual(local_date(datetime.datetime(2026, 3, 2, 8, 30)), JST_TODAY)  # noqa: DTZ001


class AccountsJstDatesTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

    @freeze_time(MONDAY_MORNING_JST)
    def test_invite_expires_counting_from_today(self):
        invite = OrganizationInvite(
            organization=self.organization, email="invitee@example.com", created_by=self.github_manager, updated_by=self.github_manager
        )
        invite.save()
        self.assertEqual(invite.expiration_date, JST_TODAY + datetime.timedelta(days=settings.ORGANIZATIONINVITE_EXPIRATION_DAYS))

    def test_attendance_record_date_is_the_jst_date_of_its_entry(self):
        # entry_datetime read back from the database is UTC
        record = AttendanceRecord(organization=self.organization, entry_datetime=UTC_MOMENT)
        record.clean()
        self.assertEqual(record.date, JST_TODAY)
