import logging
from collections.abc import Iterable

from django.conf import settings
from django.utils import timezone
from slack_sdk.web import WebClient

from ..models import KippoOrganization, KippoUser, OrganizationMembership, PersonalHoliday

logger = logging.getLogger(__name__)


def _get_persionalholidays_for_date(
    users: Iterable[KippoUser], read_behind_buffer_days: int = settings.PERSONALHOLIDAY_READ_BEHIND_BUFFER_DAYS
) -> list[tuple[KippoUser, PersonalHoliday]]:
    """
    Get PersonalHoliday entries for the last {read_behind_buffer_days} days and return,
    a dictionary of dates keyed by KippoUser
    """
    current_datetime = timezone.localtime()
    search_start_datetime = current_datetime - timezone.timedelta(days=read_behind_buffer_days)
    existing_personalholiday_entries = PersonalHoliday.objects.filter(
        user__in=users,
        day__gte=search_start_datetime.date(),
        day__lte=current_datetime.date(),
    )
    users_on_personalholiday = []
    for entry in existing_personalholiday_entries:
        if entry.day == current_datetime.date():
            # if the entry is for today, add it to the list
            users_on_personalholiday.append((entry.user, entry))
        else:
            # PersonalHoliday stored as date + duration
            # -- build dates from duration
            for i in range(1, entry.duration + 1):
                candidate_date = entry.day + timezone.timedelta(days=i)
                if candidate_date == current_datetime.date():
                    users_on_personalholiday.append((entry.user, entry))
    return users_on_personalholiday


def post_personalholidays(event: dict | None = None, context: dict | None = None) -> tuple[list, list]:  # noqa: ARG001
    """Post PersonalHoliday to Slack."""
    from commons.slackcommand.base import SubCommandBase

    # get latest attendance record for each user/organization for the today
    enabled_organizations = KippoOrganization.objects.filter(
        slack_attendance_report_channel__isnull=False,
        enable_slack_channel_reporting=True,
    )
    logger.info(f"Found {enabled_organizations.count()} enabled KippoOrganization")
    user_persionalholidays = []
    personalholidays_report_blocks = []
    for organization in enabled_organizations:
        # get organization members
        organization_memberships = list(
            OrganizationMembership.objects.filter(organization=organization).order_by("user__last_name", "user__first_name")
        )
        organization_users = [membership.user for membership in organization_memberships]
        organizationmembership_by_username = {membership.user.username: membership for membership in organization_memberships}

        user_persionalholidays = _get_persionalholidays_for_date(
            organization_users,
            read_behind_buffer_days=settings.PERSONALHOLIDAY_READ_BEHIND_BUFFER_DAYS,
        )
        logger.info(f"Found {len(user_persionalholidays)} users with PersonalHoliday in organization {organization.name}")
        if not user_persionalholidays:
            logger.info(
                f"-- if PersonalHoliday output is expected, check KippoOrganization {organization.name} settings: "
                f"slack_attendance_report_channel, enable_slack_channel_reporting"
            )
        user_status_blocks = []
        web_client = WebClient(token=organization.slack_api_token)
        for user, persionalholiday in user_persionalholidays:
            logger.info(f"User {user.username} has PersonalHoliday: {persionalholiday}")
            user_organization_membership = organizationmembership_by_username.get(user.username, None)
            user_image_url = SubCommandBase._get_user_image_url(
                web_client, user_organization_membership, refresh_days=settings.REFRESH_SLACK_IMAGE_URL_DAYS
            )

            half_day_text = "半休" if persionalholiday.is_half else "全休"
            if user_image_url:
                logger.debug(f"User {user.username} has slack_image_url: {user_image_url}")
                # Output message with user SLACK image
                user_status_blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "image",
                                "image_url": user_image_url,
                                "alt_text": user.display_name,
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*{user.display_name}* 本日 {half_day_text}します。",
                            },
                        ],
                    }
                )
            else:
                logger.warning(f"User {user.username} has no slack_image_url: {user_image_url}")
                # Output message WITHOUT user SLACK image, fallback to :white_square:
                user_status_blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f":white_square: *{user.display_name}* 本日 {half_day_text}します。",
                        },
                    }
                )
        if user_status_blocks:
            personalholidays_report_blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": (f"本日({timezone.localdate()})休む予定のメンバー"),
                    },
                }
            ]
            personalholidays_report_blocks.extend(user_status_blocks)

            # post to slack channel
            attendance_report_channel = organization.slack_attendance_report_channel
            web_send_response = web_client.chat_postMessage(channel=attendance_report_channel, blocks=personalholidays_report_blocks)
            logger.debug(
                f"Posted PersonalHoliday report to {attendance_report_channel} for organization {organization.name}, response: {web_send_response}"
            )
    serializable_user_persionalholidays = [
        {
            "user": user.username,
            "personal_holiday": {
                "day": persionalholiday.day.isoformat(),
                "duration": persionalholiday.duration,
                "is_half": persionalholiday.is_half,
            },
        }
        for user, persionalholiday in user_persionalholidays
    ]
    return serializable_user_persionalholidays, personalholidays_report_blocks
