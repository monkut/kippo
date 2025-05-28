import logging
from collections.abc import Generator

from django.utils import timezone

from .models import KippoUser, PersonalHoliday

logger = logging.getLogger(__name__)


def get_personal_holidays_generator(from_datetime: timezone.datetime | None) -> Generator:
    qs = PersonalHoliday.objects.all()
    if from_datetime:
        qs = qs.filter(day__gte=from_datetime.date())
    for personal_holiday in qs:
        yield from personal_holiday.get_weeklyeffort_hours()


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
