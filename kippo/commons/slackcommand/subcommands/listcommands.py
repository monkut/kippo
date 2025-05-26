import logging

from accounts.models import SlackCommand
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookResponse

from commons.slackcommand.base import SubCommandBase

logger = logging.getLogger(__name__)


class ListCommandsSubCommand(SubCommandBase):
    """Command to clock out a user."""

    DISPLAY_COMMAND_NAME: str = "list-commands"
    DESCRIPTION: str = "コマンドの一覧を表示します。例）`COMMAND list-commands`"
    ALIASES: set = {
        "project-status",
        "projectstatus",
        "status",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the check-in command."""
        # web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)
        raise NotImplementedError
