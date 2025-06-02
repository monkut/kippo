import datetime
import logging

from accounts.models import SlackCommand
from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.db.models.query import QuerySet
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...functions import previous_week_startdate
from ...models import ProjectWeeklyEffort

logger = logging.getLogger(__name__)


class ListProjectEffortSubCommand(SubCommandBase):
    """Command to list all registered effort for th latest week for the user"""

    DISPLAY_COMMAND_NAME: str = "list-project-effort"
    DESCRIPTION: str = "実行中プロジェクトの現状週間ステータスを表示します。例） `COMMAND list-project-status`"
    ALIASES: set = {
        "list-project-effort",
        "listprojecteffort",
        "list-effort",
        "listeffort",
    }

    @classmethod
    def _get_project_effort_blocks(cls, related_effort_entries: QuerySet[ProjectWeeklyEffort], week_start: datetime.date) -> list[dict]:
        project_effort_blocks = []
        display_week_start_date = week_start.strftime("%-m月%-d日")
        project_effort_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{display_week_start_date}週のプロジェクト稼働時間",
            },
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": "*プロジェクト名*",
                },
                {
                    "type": "mrkdwn",
                    "text": "*稼働時間*",
                },
            ],
        }

        slack_max_fields = 10
        total = 0
        for effort in related_effort_entries:
            project_name = effort.project.name
            hours = effort.hours
            project_effort_block["fields"].append({"type": "plain_text", "text": project_name})
            project_effort_block["fields"].append({"type": "plain_text", "text": str(hours)})

            if len(project_effort_block["fields"]) >= slack_max_fields:
                # If we have 10 fields, we need to create a new block
                project_effort_blocks.append(project_effort_block)
                project_effort_block = {"type": "section", "fields": []}

            total += hours
        project_effort_block["fields"].append({"type": "mrkdwn", "text": "_合計_"})
        project_effort_block["fields"].append({"type": "mrkdwn", "text": f"_{str(total)}_"})
        project_effort_blocks.append(project_effort_block)
        return project_effort_blocks

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse | None]:
        """Report current taotal project effort for the user"""
        assert cls._is_valid_subcommand_alias(command.sub_command)
        web_send_response = None

        # this is extra text provided by the user
        text_without_subcommand = command.get_text_without_subcommand()

        # check if datetime is given in 'text'
        logger.debug(f"text_without_subcommand={text_without_subcommand}")
        week_start = previous_week_startdate()
        related_effort_entries = ProjectWeeklyEffort.objects.filter(
            project__organization=command.organization, user=command.user, week_start=week_start
        ).order_by("hours")
        if not related_effort_entries:
            display_week_start_date = week_start.strftime("%-m月%-d日")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (f":warning: ({display_week_start_date}週) プロジェクト稼働時間が見つかりませんでした。\n"),
                    },
                }
            ]
        else:
            effort_status_blocks = cls._get_project_effort_blocks(related_effort_entries, week_start)
            command_response_blocks = effort_status_blocks
            command.is_valid = True
            command.save()

        webhook_send_response = None
        if command_response_blocks:
            # Notify user that notification was sent to the registered channel
            webhook_client = WebhookClient(command.response_url)
            webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)
        else:
            logger.warning(f"command_response_blocks is empty, no response sent to {command.user.username}.")
        return command_response_blocks, web_send_response, webhook_send_response
