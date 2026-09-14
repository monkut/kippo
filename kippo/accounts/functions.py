import datetime
import logging
from collections import defaultdict
from collections.abc import Collection, Generator, Iterable
from typing import Any

from commons.definitions import PERSONAL_HOLIDAY_LOOKBACK_DAYS
from django.utils import timezone

from .models import Country, KippoUser, OrganizationMembership, PersonalHoliday, PublicHoliday

logger = logging.getLogger(__name__)


def get_personal_holidays_generator(from_datetime: timezone.datetime | None) -> Generator:
    qs = PersonalHoliday.objects.all()
    if from_datetime:
        qs = qs.filter(day__gte=from_datetime.date())
    for personal_holiday in qs:
        yield from personal_holiday.get_weeklyeffort_hours()


def get_allholiday_weekstarts(
    memberships: Iterable[OrganizationMembership],
    week_starts: Collection[datetime.date],
    default_holiday_country: Country | None = None,
) -> dict[Any, set[datetime.date]]:
    """Return ``{user_id: {week_start, ...}}`` for weeks in which the member has NO workday.

    A week has no workday when every one of the member's committed weekdays is covered by a
    PublicHoliday (for the member's holiday_country, falling back to `default_holiday_country`)
    or by one of the member's PersonalHoliday entries. Such a week has no effort to enter, so
    it must not be reported as missing.

    Queries are batched over all `memberships` to avoid N+1.
    """
    memberships = list(memberships)
    if not memberships or not week_starts:
        return {}

    window_start = min(week_starts)
    window_end = max(week_starts) + datetime.timedelta(days=6)
    user_ids = [membership.user_id for membership in memberships]

    # PersonalHoliday.duration is INCLUSIVE of `day`, and the filter matches the holiday's START
    # date -- look back so a span beginning before the window still contributes its in-window days.
    personal_holiday_dates: dict[Any, set[datetime.date]] = defaultdict(set)
    personal_holidays = PersonalHoliday.objects.filter(
        user_id__in=user_ids,
        day__gte=window_start - datetime.timedelta(days=PERSONAL_HOLIDAY_LOOKBACK_DAYS),
        day__lte=window_end,
    )
    for holiday in personal_holidays:
        for offset in range(max(1, holiday.duration)):
            holiday_date = holiday.day + datetime.timedelta(days=offset)
            if window_start <= holiday_date <= window_end:
                personal_holiday_dates[holiday.user_id].add(holiday_date)

    country_ids = {membership.user.holiday_country_id or getattr(default_holiday_country, "id", None) for membership in memberships}
    country_ids.discard(None)
    public_holiday_dates: dict[Any, set[datetime.date]] = defaultdict(set)
    if country_ids:
        public_holidays = PublicHoliday.objects.filter(country_id__in=country_ids, day__gte=window_start, day__lte=window_end)
        for holiday in public_holidays:
            public_holiday_dates[holiday.country_id].add(holiday.day)

    results = {}
    for membership in memberships:
        country_id = membership.user.holiday_country_id or getattr(default_holiday_country, "id", None)
        holiday_dates = personal_holiday_dates[membership.user_id] | public_holiday_dates[country_id]
        committed_weekdays = membership.committed_weekdays
        if not holiday_dates or not committed_weekdays:  # nothing to exclude / no committed days to evaluate
            continue
        allholiday_weekstarts = {
            week_start
            for week_start in week_starts
            if all(week_start + datetime.timedelta(days=weekday) in holiday_dates for weekday in committed_weekdays)
        }
        if allholiday_weekstarts:
            results[membership.user_id] = allholiday_weekstarts
    return results


def process_organizationinvites(backend: str, user: KippoUser, response: dict | object, *args, **kwargs):  # noqa: ARG001
    """Check if the user has any invites and send them to the user."""
    new_user_check_buffer = timezone.now() - timezone.timedelta(minutes=5)  # don't want to query DB if user is not new
    if hasattr(user, "email") and user.email and hasattr(user, "date_joined") and user.date_joined >= new_user_check_buffer:
        from accounts.models import OrganizationInvite

        # get all organization memberships for the user
        today = timezone.localdate()
        incomplete_invites = OrganizationInvite.objects.filter(email=user.email, expiration_date__gte=today, is_complete=False)
        for invite in incomplete_invites:
            invite.create_organizationmembership(user)
        # if the user has no incomplete invites, check if there are any expired invites
        logger.info("User has no incomplete invites, may be expired.")

    else:
        logger.error("User has no email address, cannot process organization invites.")
