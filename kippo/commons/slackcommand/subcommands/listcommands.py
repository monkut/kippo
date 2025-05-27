import logging

from accounts.models import SlackCommand
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...definitions import SlackResponseTypes
from ..base import SubCommandBase

logger = logging.getLogger(__name__)


class ListCommandsSubCommand(SubCommandBase):
    """Command to clock out a user."""

    DISPLAY_COMMAND_NAME: str = "list-commands"
    DESCRIPTION: str = "コマンドの一覧を表示します。例） `COMMAND list-commands`"
    ALIASES: set = {
        "list-commands",
        "listcommands",
        "commands",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the check-in command."""
        from .. import get_all_subcommands

        web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)

        sub_command_classes = get_all_subcommands()

        command_response_blocks = []
        for sub_command_class in sub_command_classes:
            logger.debug(f"Adding sub_command: {sub_command_class.__name__} {sub_command_class.DISPLAY_COMMAND_NAME}")
            aliases_str = ", ".join(sub_command_class.ALIASES)
            command_response_block = {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (f"*{sub_command_class.DISPLAY_COMMAND_NAME}* - {sub_command_class.DESCRIPTION}\nALIASES: {aliases_str}"),
                },
            }
            command_response_blocks.append(command_response_block)

        # Notify user that notification was sent to the registered channel
        webhook_client = WebhookClient(command.response_url)
        webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)

        return command_response_blocks, web_send_response, webhook_send_response
