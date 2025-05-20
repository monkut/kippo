import logging

from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.conf import settings
from django.utils import timezone
from slack_sdk.web import SlackResponse, WebClient
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...definitions import AttendanceRecordCategory
from ...models import AttendanceRecord, SlackCommand

logger = logging.getLogger(__name__)


class ClockInSubCommand(SubCommandBase):
    """Command to check in a user."""

    ALIASES: set = {
        "開始",
        "clockin",
        "clock-in",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the clock-in command."""
        web_send_response = None
        assert cls._is_valid_subcommand_alias(command.sub_command)

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

        if latest_attendance_record and latest_attendance_record.category == AttendanceRecordCategory.START:
            # Respond to user that they have already checked in today
            local_created_datetime = latest_attendance_record.created_datetime.astimezone(settings.JST)
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"すでに出勤済みです。:warning: {local_created_datetime}に出勤済みです。\n"
                            f"`{organization_command_name} clockout MM/DD HH:MM`で退勤してから、出勤してください。",
                        ),
                    },
                }
            ]

        else:
            # check if datetime is given in 'text'
            # check if full year is given in 'text'
            # MM/DD HH:MM or MM-DD HH:MM
            if text_without_subcommand.count(":"):
                if text_without_subcommand.count("/") == 1:
                    # add year to text_without_subcommand
                    text_without_subcommand = f"{timezone.localdate().year}/{text_without_subcommand}"
                elif text_without_subcommand.count("-") == 1:
                    # add year to text_without_subcommand
                    text_without_subcommand = f"{timezone.localdate().year}-{text_without_subcommand}"
            entry_datetime = cls._get_datetime_from_text(text_without_subcommand)

            send_channel_notification = False
            if not entry_datetime:
                send_channel_notification = True
                entry_datetime = timezone.localtime()

            record = AttendanceRecord(
                created_by=command.user,
                updated_by=command.user,
                organization=command.organization,
                category=AttendanceRecordCategory.START,
                entry_datetime=entry_datetime,
            )
            record.save()

            attendance_report_channel = command.organization.slack_attendance_report_channel
            if not send_channel_notification:
                command_response_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"`{entry_datetime}`の出勤記録を登録しました。\n(時間指定の登録は、{attendance_report_channel}へ通知は行いません)",
                            ),
                        },
                    }
                ]
            else:
                # Prepare the response message
                attendance_notification_blocks = []
                attendance_notification_block = {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{command.user.display_name}* 出勤しました！\n{text_without_subcommand}",
                    },
                }
                attendance_notification_blocks.append(attendance_notification_block)

                web_client = WebClient(token=command.organization.slack_api_token)
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
