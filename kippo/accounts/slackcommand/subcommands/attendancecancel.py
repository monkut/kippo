import datetime
import logging

from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.conf import settings
from django.utils import timezone
from django.utils.text import gettext_lazy as _
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...models import AttendanceRecord, SlackCommand

logger = logging.getLogger(__name__)


class AttendanceCancelSubCommand(SubCommandBase):
    """Command to list status of organization users."""

    DISPLAY_COMMAND_NAME: str = "attendance-cancel"
    DESCRIPTION: str = _("直近の（５分）出勤や休憩の記録をキャンセル・削除 例） `COMMAND attendance-cancel`")
    ALIASES: set = {
        "attendancecancel",
        "attendance-cancel",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Cancel/Delete the latest (last 5 minutes) attendance record for the user."""
        web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)

        # get latest attendance record for each user/organization for the today
        min_datetime = timezone.localtime() - datetime.timedelta(minutes=settings.ATTENDANCECANCEL_SUBCOMMAND_MINUTES)
        latest_user_attendance_record = (
            AttendanceRecord.objects.filter(
                created_by=command.user,
                organization=command.organization,
                entry_datetime__gte=min_datetime,
            )
            .order_by("-entry_datetime")
            .first()
        )

        if latest_user_attendance_record:
            # Delete the latest attendance record
            local_created_datetime = latest_user_attendance_record.created_datetime.astimezone(settings.JST)
            local_created_datetime_display_str = local_created_datetime.strftime("%-m/%-d %-H:%M")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"({local_created_datetime_display_str}) {latest_user_attendance_record.category}の記録を削除しました。",
                    },
                }
            ]
            logger.info(
                f"Deleting latest AttendanceRecord for user {command.user} in organization {command.organization} {latest_user_attendance_record} ..."
            )
            latest_user_attendance_record.delete()
            logger.info(
                f"Deleting latest AttendanceRecord for user {command.user} in organization "
                f"{command.organization} {latest_user_attendance_record} ... DONE"
            )

        else:
            logger.warning(f"No AttendenceRecord(s) found for organization {command.organization} on {min_datetime}")
            min_datetime_display_str = min_datetime.astimezone(settings.JST).strftime("%-m/%-d %-H:%M")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f">={min_datetime_display_str}の記録がみつかりません。",
                    },
                }
            ]

        # Notify user that notification was sent to the registered channel
        webhook_client = WebhookClient(command.response_url)
        webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)

        return command_response_blocks, web_send_response, webhook_send_response
