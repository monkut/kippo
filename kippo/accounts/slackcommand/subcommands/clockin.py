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


class ClockInSubCommand(SubCommandBase):
    """Command to check in a user."""

    DISPLAY_COMMAND_NAME: str = "clock-in"
    DESCRIPTION: str = _("出勤情報を登録。例） `COMMAND clock-in`")
    ALIASES: set = {
        "出勤",
        "開始",
        "clockin",
        "clock-in",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the clock-in command."""
        web_send_response = None
        assert cls._is_valid_subcommand_alias(command.sub_command)
        assert command.user, f"user not defined for command: sub_command={command.sub_command}, text={command.text}"

        # this is extra text provided by the user
        text_without_subcommand = cls._get_text_without_subcommand(command)
        organization_command_name = command.organization.slack_command_name

        # get latest attendance record for the user/organization
        latest_attendance_record = (
            AttendanceRecord.objects.filter(
                created_by=command.user,
                organization=command.organization,
            )
            .order_by("-entry_datetime")
            .first()
        )
        invalid_categories = (AttendanceRecordCategory.START, AttendanceRecordCategory.BREAK_START, AttendanceRecordCategory.BREAK_END)
        if latest_attendance_record and latest_attendance_record.category in invalid_categories:
            # Respond to user that they have already checked in today
            logger.warning(
                f"INVALID category {latest_attendance_record.category} for user {command.user} in organization {command.organization} to `clock-in`"
            )
            local_created_datetime = latest_attendance_record.created_datetime.astimezone(settings.JST)
            local_created_datetime_display_str = local_created_datetime.strftime("%-m/%-d %-H:%M")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":warning: すでにに出勤中です。{local_created_datetime_display_str}\n"
                            f"`/{organization_command_name} clockout MM/DD HH:MM`で退勤してから、出勤してください。"
                        ),
                    },
                }
            ]

        else:
            entry_datetime = cls._get_datetime_from_text(text_without_subcommand)

            send_channel_notification = False
            if not entry_datetime:
                send_channel_notification = True
                entry_datetime = timezone.localtime()
            logger.info(
                f"Creating AttendanceRecord({AttendanceRecordCategory.START.value}) for user {command.user.username} "
                f"in organization {command.organization.name} at {entry_datetime} ..."
            )
            record = AttendanceRecord(
                created_by=command.user,
                updated_by=command.user,
                organization=command.organization,
                category=AttendanceRecordCategory.START,
                entry_datetime=entry_datetime,
            )
            record.save()
            logger.info(
                f"Creating AttendanceRecord({AttendanceRecordCategory.START.value}) for user {command.user.username} "
                f"in organization {command.organization.name} at {entry_datetime} ... DONE"
            )

            attendance_report_channel = command.organization.slack_attendance_report_channel
            if not send_channel_notification:
                command_response_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"`{entry_datetime}`の出勤記録を登録しました。\n(時間指定の登録は、{attendance_report_channel}へ通知しません)",
                        },
                    }
                ]
            else:
                # Prepare the response message
                web_client = WebClient(token=command.organization.slack_api_token)
                user_image_url = None
                attendance_notification_blocks = []
                user_organization_membership = OrganizationMembership.objects.filter(user=command.user, organization=command.organization).first()
                if user_organization_membership:
                    user_image_url = cls._get_user_image_url(
                        web_client, user_organization_membership, refresh_days=settings.REFRESH_SLACK_IMAGE_URL_DAYS
                    )
                    if user_image_url:
                        logger.debug(f"User {record.created_by.username} has slack_image_url: {user_image_url}")
                        # Output message with user SLACK image
                        attendance_notification_blocks.append(
                            {
                                "type": "context",
                                "elements": [
                                    {
                                        "type": "image",
                                        "image_url": user_image_url,
                                        "alt_text": command.user.display_name,
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*{command.user.display_name}* 出勤しました！ {text_without_subcommand} ",
                                    },
                                ],
                            }
                        )

                if not attendance_notification_blocks:
                    logger.warning(f"User {command.user.display_name} has no slack_image_url: {user_image_url}")
                    # Output message WITHOUT user SLACK image, fallback to :white_square:
                    attendance_notification_blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*{command.user.display_name}* 出勤しました！\n{text_without_subcommand}",
                            },
                        }
                    )

                web_send_response = web_client.chat_postMessage(channel=attendance_report_channel, blocks=attendance_notification_blocks)
                command_response_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"`{attendance_report_channel}`チャンネルに通知をしました。",
                        },
                    }
                ]
            command.is_valid = True
            command.save()
        # Notify user that notification was sent to the registered channel
        webhook_client = WebhookClient(command.response_url)
        webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)
        return command_response_blocks, web_send_response, webhook_send_response
