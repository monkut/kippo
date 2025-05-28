import logging

from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.conf import settings
from django.utils import timezone
from django.utils.text import gettext_lazy as _
from slack_sdk.web import SlackResponse, WebClient
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...definitions import AttendanceRecordCategory
from ...models import AttendanceRecord, SlackCommand

logger = logging.getLogger(__name__)


class BreakEndSubCommand(SubCommandBase):
    """Command to check in a user."""

    DISPLAY_COMMAND_NAME: str = "break-end"
    DESCRIPTION: str = _("休憩終了を登録。例） `COMMAND break-end`")
    ALIASES: set = {
        "休憩終了",
        "再開",
        "breakend",
        "break-end",
    }

    @classmethod
    def _prepare_valid_response_blocks(cls, command: SlackCommand, text_without_subcommand: str) -> tuple[list[dict], SlackResponse | None]:
        # VALID: User has started work, can take a break
        entry_datetime = cls._get_datetime_from_text(text_without_subcommand)
        web_send_response = None
        message = None
        send_channel_notification = False
        current_localtime = timezone.localtime()
        comment_display_text = f"\n> {text_without_subcommand}" if text_without_subcommand else ""
        if not entry_datetime:
            logger.info(f"`entry_datetime` not parsed from text (expected YYYY/MM/DD): {text_without_subcommand}")
            send_channel_notification = True
            entry_datetime = current_localtime
            message = f"*{command.user.display_name}* 休憩終了、再開します。{comment_display_text}"

        is_future_datetime = entry_datetime and entry_datetime > current_localtime
        if is_future_datetime:
            if entry_datetime.date() == timezone.localdate():
                send_channel_notification = True
                display_time = entry_datetime.strftime("%H:%M")
                message = f"*{command.user.display_name}* {display_time} に休憩終了、再開する予定です。{comment_display_text}"
            else:
                logger.warning(f"entry_datetime({entry_datetime}) is not today({current_localtime}), message will not be sent.")

        logger.info(
            f"Creating '{AttendanceRecordCategory.BREAK_START.value}' AttendanceRecord {command.organization.name} {command.user.username} ..."
        )
        record = AttendanceRecord(
            created_by=command.user,
            updated_by=command.user,
            organization=command.organization,
            category=AttendanceRecordCategory.BREAK_END,
            entry_datetime=entry_datetime,
        )
        record.save()
        logger.info(
            f"Creating '{AttendanceRecordCategory.BREAK_START.value}' AttendanceRecord {command.organization.name} {command.user.username} ... DONE"
        )

        attendance_report_channel = command.organization.slack_attendance_report_channel

        # Prepare the response message
        entry_datetime_display_str = entry_datetime.strftime("%-m/%-d %-H:%M")
        elapsed_duration = record.get_duration_timedelta()
        duration_display_str = f"\n> {elapsed_duration.total_seconds() / 60 / 60:.1f}h 経過"  # 10.383723 -> 10.4h
        command_response_message = (
            f"休憩終了を登録しました\n日付（{entry_datetime_display_str}）が異なるため、 "
            f"`{attendance_report_channel}` チャンネルに通知されません。{duration_display_str}"
        )
        if send_channel_notification:
            assert message, "Message must be set when sending channel notification."
            attendance_notification_blocks = []
            web_client = WebClient(token=command.organization.slack_api_token)
            user_organization_membership = command.get_user_organization_membership()
            attendance_notification_block = cls._prepare_message_block_with_user_image(
                message=message, web_client=web_client, user_organization_membership=user_organization_membership
            )
            attendance_notification_blocks.append(attendance_notification_block)

            web_send_response = web_client.chat_postMessage(channel=attendance_report_channel, blocks=attendance_notification_blocks)
            command_response_message = f"休憩終了を登録しました\n`{attendance_report_channel}` チャンネルに通知をしました。{duration_display_str}"
        command_response_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": command_response_message,
                },
            }
        ]
        command.is_valid = True
        command.save()
        return command_response_blocks, web_send_response

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the break-start command."""
        web_send_response = None
        assert cls._is_valid_subcommand_alias(command.sub_command)

        # this is extra text provided by the user
        text_without_subcommand = command.get_text_without_subcommand()
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
        logger.debug(f"latest_attendance_record={latest_attendance_record}, category={latest_attendance_record.category}")

        if latest_attendance_record and latest_attendance_record.category == AttendanceRecordCategory.BREAK_START:
            logger.info(f"Valid latest_attendance_record.category='{latest_attendance_record.category}'")
            command_response_blocks, web_send_response = cls._prepare_valid_response_blocks(command, text_without_subcommand)
        elif latest_attendance_record and latest_attendance_record.category == AttendanceRecordCategory.BREAK_END:
            # INVALID: User is already on a break, cannot take another break
            local_created_datetime = latest_attendance_record.created_datetime.astimezone(settings.JST)
            local_created_datetime_display_str = local_created_datetime.strftime("%-m/%-d %-H:%M")
            message = (
                f":warning: すでに休憩終了しています。\n最新休憩記録 > {latest_attendance_record.category} {local_created_datetime_display_str}\n"
            )
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": message,
                    },
                }
            ]
        else:
            logger.warning("INVALID: `break-start` record not found, cannot call `break-end` successfully.")
            if latest_attendance_record:
                # User has a record, but it's not a START record
                logger.warning(f"User {command.user.username} has latest attendance record: {latest_attendance_record.category}")
                local_created_datetime = latest_attendance_record.created_datetime.astimezone(settings.JST)
                local_created_datetime_display_str = local_created_datetime.strftime("%-m/%-d %-H:%M")
                message = (
                    f":warning: 休憩中になっていません！\n"
                    f"最新出勤記録 > {latest_attendance_record.category} {local_created_datetime_display_str}\n"
                    f"`/{organization_command_name} break-start MM/DD HH:MM`で休憩開始してから、完了してください。"
                )
            else:
                message = (
                    f":warning: 休憩中になっていません！。\n"
                    f"休憩開始記録がありません。\n"
                    f"`/{organization_command_name} break-start MM/DD HH:MM`で休憩開始してから、完了してください。"
                )
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": message,
                    },
                }
            ]

        # Notify user that notification was sent to the registered channel
        webhook_client = WebhookClient(command.response_url)
        webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)
        return command_response_blocks, web_send_response, webhook_send_response
