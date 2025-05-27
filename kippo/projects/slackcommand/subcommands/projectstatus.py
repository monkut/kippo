import logging

from accounts.models import SlackCommand
from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.utils.text import gettext_lazy as _
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...models import ActiveKippoProject, KippoProjectStatus

logger = logging.getLogger(__name__)


class ProjectStatusSubCommand(SubCommandBase):
    """Command to clock out a user."""

    DISPLAY_COMMAND_NAME: str = "project-status"
    DESCRIPTION: str = _("チャンネルのプロジェクトへステータスを当露光。例) `COMMAND project-status {STATUS COMMENT}`")
    ALIASES: set = {
        "project-status",
        "projectstatus",
        "status",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the check-in command."""
        web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)
        project_report_channel = command.organization.slack_weekly_project_report_channel

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
            logger.info(f"{command.organization.name} linked project found, '{related_project.name}', creating KippoProjectStatus ...")
            # register new KippoProjectStatus
            kippo_project_status = KippoProjectStatus(
                project=related_project,
                created_by=command.user,
                updated_by=command.user,
                comment=text_without_subcommand,
            )
            kippo_project_status.save()
            logger.info(f"{command.organization.name} linked project found, '{related_project.name}', creating KippoProjectStatus ... DONE")

            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"> {text_without_subcommand}\n{related_project.name}にステータスを登録しました。\n"
                            f"週開始時に、まとめて、{project_report_channel}チャンネルにサマリが送られます。"
                        ),
                    },
                }
            ]
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
