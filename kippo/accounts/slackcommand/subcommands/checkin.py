import logging

from commons.definitions import SlackResponseTypes
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...definitions import AttendanceRecordCategory
from ...models import AttendanceRecord, SlackCommand
from .base import SubCommandBase

logger = logging.getLogger(__name__)


class CheckInSubCommand(SubCommandBase):
    """Command to check in a user."""

    ALIASES = {
        "開始",
        "clockin",
        "clock-in",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], WebhookResponse]:
        """Handle the check-in command."""
        # this is extra text provided by the user
        text_without_subcommand = command.text.split(command.sub_command, 1)[-1].strip()

        record = AttendanceRecord(
            user=command.user,
            organization=command.organization,
            category=AttendanceRecordCategory.START,
        )
        record.save()

        # Prepare the response message
        command_response_blocks = []
        attendance_notification_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{command.user.display_name}* 出勤しました！\n{text_without_subcommand}",
            },
        }
        command_response_blocks.append(attendance_notification_block)

        client = WebhookClient(command.response_url)
        send_response = client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.IN_CHANNEL)

        return command_response_blocks, send_response
