import logging

from accounts.models import SlackCommand
from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.conf import settings
from django.utils import timezone
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...functions import current_week_startdate
from ...models import ActiveKippoProject
from ..managers import ProjectSlackManager

logger = logging.getLogger(__name__)


class ListProjectStatusSubCommand(SubCommandBase):
    """Command to clock out a user."""

    DISPLAY_COMMAND_NAME: str = "list-project-status"
    DESCRIPTION: str = "実行中プロジェクトの現状週間ステータスを表示します。例） `COMMAND list-project-status`"
    ALIASES: set = {
        "list-project-status",
        "listprojectstatus",
        "list-status",
        "liststatus",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse | None]:
        """Report current project status for current project channel"""
        assert cls._is_valid_subcommand_alias(command.sub_command)
        web_send_response = None

        # this is extra text provided by the user
        text_without_subcommand = cls._get_text_without_subcommand(command)

        # check if datetime is given in 'text'
        logger.debug(f"text_without_subcommand={text_without_subcommand}")

        source_channel = command.payload.get("channel_name", None)
        # ActiveKippoProject includes filters:
        # > is_closed=False
        # > display_as_active=True
        related_project = ActiveKippoProject.objects.filter(organization=command.organization, slack_channel_name=source_channel).first()
        if not related_project:
            logger.error(f"{command.organization.name} Project not found for source_channel: {source_channel}")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"プロジェクトが見つかりませんでした。:warning: `{source_channel}`チャンネルは、プロジェクトに関連付けられていません。\n"
                            f"（閉じている可能性があります）\n"
                            f"プロジェクトの`slack_channel_name`設定を確認してください。"
                        ),
                    },
                }
            ]
        else:
            # get "current" KippoProjectStatus
            # -- define "current" as the latest status for the project
            week_start_date = current_week_startdate()
            time_deadline = command.organization.weekly_project_time_deadline
            week_start_datetime = timezone.datetime.combine(week_start_date, time_deadline, tzinfo=settings.JST)

            manager = ProjectSlackManager(organization=command.organization)
            user_comments = manager._get_project_user_comments(
                week_start_datetime=week_start_datetime,
                project=related_project,
            )
            project_status_blocks = manager._prepare_project_status_blocks(
                project=related_project,
                user_comments=user_comments,
            )
            command_response_blocks = project_status_blocks
            command.is_valid = True
            command.save()

        webhook_send_response = None
        if command_response_blocks:
            # Notify user that notification was sent to the registered channel
            logger.debug(f"command_response_blocks={command_response_blocks}")
            webhook_client = WebhookClient(command.response_url)
            webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)
            logger.debug(f"webhook_send_response={webhook_send_response.status_code}, {webhook_send_response.body}")
        else:
            logger.warning(f"command_response_blocks is empty, no response sent to {command.user.username}.")
        return command_response_blocks, web_send_response, webhook_send_response
