import logging

from accounts.models import SlackCommand
from commons.slackcommand.base import SubCommandBase
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookResponse

logger = logging.getLogger(__name__)


class ListProjectStatusSubCommand(SubCommandBase):
    """Command to clock out a user."""

    DISPLAY_COMMAND_NAME: str = "list-project-status"
    DESCRIPTION: str = "実行中プロジェクトの週間ステータスを表示します。例）`COMMAND list-project-status`"
    ALIASES: set = {
        "list-project-status",
        "listprojectstatus",
        "list-status",
        "liststatus",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the check-in command."""
        # web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)
        raise NotImplementedError
