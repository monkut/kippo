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


class ClockOutSubCommand(SubCommandBase):
    """Command to clock out a user."""

    DISPLAY_COMMAND_NAME: str = "clock-out"
    DESCRIPTION: str = _("退勤情報を登録。例) `COMMAND clock-out`")
    ALIASES: set = {
        "退勤",
        "終了",
        "終わります",
        "clockout",
        "clock-out",
    }

    @classmethod
    def _get_invalid_message(cls, category: AttendanceRecordCategory, organization_command_name: str) -> str:
        """Return a message based on the invalid category."""
        if category == AttendanceRecordCategory.BREAK_START:
            message = f"`/{organization_command_name} break-end`で休暇終了してから、退勤してください。"
        elif category == AttendanceRecordCategory.END:
            message = f"`/{organization_command_name} clockin YY/MM/DD HH:MM`で出勤してから、退勤してください。"
        else:
            raise ValueError(f"Invalid category {category} for clock-out command.")
        return message

    @classmethod
    def _prepare_valid_response_blocks(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None]:
        """Prepare response blocks for a valid clock-out."""
        web_send_response = None
        text_without_subcommand = cls._get_text_without_subcommand(command)
        # check if datetime is given in 'text'
        logger.debug(f"text_without_subcommand={text_without_subcommand}")
        entry_datetime = cls._get_datetime_from_text(text_without_subcommand)

        send_channel_notification = False
        if not entry_datetime:
            send_channel_notification = True
            entry_datetime = timezone.localtime()
            logger.debug(f">>> `entry_datetime` not given, using localtime: {entry_datetime}")
        else:
            logger.warning(f">>> Using user defined `entry_datetime`: {entry_datetime}")

        logger.debug(
            f"Creating AttendanceRecord: "
            f"entry_datetime={entry_datetime}, "
            f"created_by={command.user}, "
            f"organization={command.organization}, "
            f"category={AttendanceRecordCategory.END.value} ..."
        )
        record = AttendanceRecord(
            created_by=command.user,
            updated_by=command.user,
            organization=command.organization,
            category=AttendanceRecordCategory.END,
            entry_datetime=entry_datetime,
        )
        record.save()
        logger.debug(
            f"Creating AttendanceRecord: "
            f"entry_datetime={entry_datetime}, "
            f"created_by={command.user}, "
            f"organization={command.organization}, "
            f"category={AttendanceRecordCategory.END.value} ... DONE"
        )

        attendance_report_channel = command.organization.slack_attendance_report_channel
        if not send_channel_notification:
            entry_datetime_display_str = entry_datetime.strftime("%Y/%-m/%-d %-H:%M")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"`{entry_datetime_display_str}`の退勤記録を登録しました。\n(時間指定の登録は、{attendance_report_channel}へ通知しません)",
                        ),
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
                user_image_url = cls._get_user_image_url(web_client, user_organization_membership, refresh_days=settings.REFRESH_SLACK_IMAGE_URL_DAYS)
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
                                    "text": f"*{command.user.display_name}* 退勤しました！\n> {text_without_subcommand} ",
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
                            "text": f"*{command.user.display_name}* 退勤しました！\n> {text_without_subcommand}",
                        },
                    }
                )

            web_send_response = web_client.chat_postMessage(channel=attendance_report_channel, blocks=attendance_notification_blocks)

            command_response_blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"`{attendance_report_channel}`チャンネルに通知をしました。"}}
            ]
        command.is_valid = True
        command.save()
        return command_response_blocks, web_send_response

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the check-in command."""
        web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)

        # this is extra text provided by the user
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

        if latest_attendance_record:
            invalid_categories = (AttendanceRecordCategory.BREAK_START, AttendanceRecordCategory.END)
            if latest_attendance_record.category in invalid_categories:
                logger.warning(
                    f"Found existing END record for user {command.user} in organization {command.organization} at: "
                    f"{latest_attendance_record.entry_datetime}"
                )
                # Respond to user that they have already checked in today
                message = cls._get_invalid_message(category=latest_attendance_record.category, organization_command_name=organization_command_name)

                local_created_datetime = latest_attendance_record.created_datetime.astimezone(settings.JST)
                local_created_datetime_display_str = local_created_datetime.strftime("%-m/%-d %-H:%M")
                command_response_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (f":warning: {local_created_datetime_display_str}にすでに退勤中です。\n{message}"),
                        },
                    }
                ]

            else:
                assert latest_attendance_record.category in (AttendanceRecordCategory.START, AttendanceRecordCategory.BREAK_END)
                command_response_blocks, web_send_response = cls._prepare_valid_response_blocks(command)
        else:
            logger.warning(
                f"No existing attendance record found for user {command.user} in organization {command.organization}, expecting START record."
            )
            # Respond to user that they have already checked in today
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"出勤記録がみつかりません。`/{organization_command_name} clockin YY/MM/DD HH:MM`で出勤してから、退勤してください。",
                    },
                }
            ]

        # Notify user that notification was sent to the registered channel
        webhook_client = WebhookClient(command.response_url)
        webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)
        return command_response_blocks, web_send_response, webhook_send_response
