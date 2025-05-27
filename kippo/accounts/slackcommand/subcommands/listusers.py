import datetime
import logging

from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.conf import settings
from django.utils import timezone
from django.utils.text import gettext_lazy as _
from slack_sdk.web import SlackResponse, WebClient
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...definitions import AttendanceRecordCategory
from ...models import AttendanceRecord, OrganizationMembership, SlackCommand

logger = logging.getLogger(__name__)


class ListUsersSubCommand(SubCommandBase):
    """Command to list status of organization users."""

    DISPLAY_COMMAND_NAME: str = "list-users"
    DESCRIPTION: str = _("ユーザの出勤状況を表示。例) `COMMAND list-users`")
    ALIASES: set = {
        "listusers",
        "list-users",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the check-in command."""
        web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)

        # get latest attendance record for each user/organization for the today
        today_start_datetime = datetime.datetime.combine(timezone.localdate(), datetime.time.min).replace(tzinfo=settings.JST)
        latest_user_attendance_records = list(
            AttendanceRecord.objects.filter(
                organization=command.organization,
                entry_datetime__gte=today_start_datetime,
            )
            .order_by("created_by", "-entry_datetime")
            .distinct("created_by")
        )

        if latest_user_attendance_records:
            category_display_mapping = {
                AttendanceRecordCategory.START: "出勤中",
                AttendanceRecordCategory.END: "退勤中",
                AttendanceRecordCategory.BREAK_START: "出勤中 (休憩中)",
                AttendanceRecordCategory.BREAK_END: "出勤中",
            }
            users = [record.created_by for record in latest_user_attendance_records]

            organizationmembership_by_username = {
                m.user.username: m for m in OrganizationMembership.objects.filter(organization=command.organization, user__in=users)
            }

            user_status_blocks = []
            web_client = WebClient(token=command.organization.slack_api_token)
            for record in latest_user_attendance_records:
                user_organization_membership = organizationmembership_by_username.get(record.created_by.username, None)
                user_image_url = cls._get_user_image_url(web_client, user_organization_membership, refresh_days=settings.REFRESH_SLACK_IMAGE_URL_DAYS)

                local_created_datetime = record.created_datetime.astimezone(settings.JST)
                local_created_datetime_display = local_created_datetime.strftime("%-m/%-d %-H:%M")
                category_display = category_display_mapping.get(record.category, record.category)
                if user_image_url:
                    logger.debug(f"User {record.created_by.username} has slack_image_url: {user_image_url}")
                    # Output message with user SLACK image
                    user_status_blocks.append(
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "image",
                                    "image_url": user_image_url,
                                    "alt_text": record.created_by.display_name,
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*{record.created_by.display_name}* {local_created_datetime_display}: {category_display} ",
                                },
                            ],
                        }
                    )
                else:
                    logger.warning(f"User {record.created_by.username} has no slack_image_url: {user_image_url}")
                    # Output message WITHOUT user SLACK image, fallback to :white_square:
                    user_status_blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f":white_square: *{record.created_by.display_name}* {local_created_datetime_display}: {category_display}",
                            },
                        }
                    )

            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"今日（{today_start_datetime.date()}）の出勤記録:\n",
                    },
                }
            ]
            command_response_blocks.extend(user_status_blocks)

        else:
            logger.warning(f"No AttendenceRecord(s) found for organization {command.organization} on {today_start_datetime}")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"今日（{today_start_datetime.date()}）出勤記録がみつかりません。",
                    },
                }
            ]

        # Notify user that notification was sent to the registered channel
        webhook_client = WebhookClient(command.response_url)
        webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)
        return command_response_blocks, web_send_response, webhook_send_response
