import logging
import re

from accounts.models import SlackCommand
from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.utils.text import gettext_lazy as _
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...functions import previous_week_startdate
from ...models import ActiveKippoProject, ProjectWeeklyEffort

logger = logging.getLogger(__name__)


class ProjectEffortSubCommand(SubCommandBase):
    """Command to add ProjectWeeklyEffort to related KippoProject."""

    DISPLAY_COMMAND_NAME: str = "project-effort"
    DESCRIPTION: str = _("チャンネルのプロジェクトへ稼働時間を登録。例) `COMMAND project-effort {HOURS}`")
    ALIASES: set = {
        "project-effort",
        "effort",
        "稼働",
    }

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the project-effort command."""
        web_send_response = None
        assert cls._is_valid_subcommand_alias(command.sub_command)

        # this is extra text provided by the user
        text_without_subcommand = command.get_text_without_subcommand()

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
            # check for valid hours input
            pattern = r"^(?P<hours>\d+)(\s.+|)$"  # Matches integers or decimals
            match = re.match(pattern, text_without_subcommand)
            if match:
                max_weekly_effort_hours = 7 * 24  # 7 days * 24 hours

                # valid input, extract hours
                hours = int(match.groupdict()["hours"])
                logger.debug(f"Extracted hours: {hours} from text_without_subcommand: {text_without_subcommand}")
                if not (0 < hours <= max_weekly_effort_hours):
                    # invalid hours input, return error message
                    organization_slack_command = command.organization.slack_command_name
                    command_response_blocks = [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"> {text_without_subcommand}\n"
                                    f"稼働時間を取得できませんでした。 `/{organization_slack_command} {cls.DISPLAY_COMMAND_NAME} HOURS` "
                                    f"(0 < x < {max_weekly_effort_hours})整数で稼働時間を指定してください。"
                                ),
                            },
                        }
                    ]

                else:
                    week_start_date = previous_week_startdate()
                    # check for existing ProjectWeeklyEffort for the project/user/week_start
                    existing_effort = ProjectWeeklyEffort.objects.filter(
                        project=related_project, user=command.user, week_start=week_start_date
                    ).first()
                    display_week_start_date = week_start_date.strftime("%-m月%-d日")
                    if not existing_effort:
                        # create new ProjectWeeklyEffort)
                        logger.info(
                            f"{command.organization.name} linked project('{related_project.name}') found, and hours({hours}) detected, "
                            f"creating ProjectWeeklyEffort ({week_start_date}) ..."
                        )
                        effort = ProjectWeeklyEffort(
                            project=related_project,
                            week_start=week_start_date,
                            user=command.user,
                            hours=hours,
                        )
                        effort.save()
                        logger.info(
                            f"{command.organization.name} linked project('{related_project.name}') found, and hours({hours}) detected, "
                            f"creating ProjectWeeklyEffort ({week_start_date}) ... DONE"
                        )

                        command_response_blocks = [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (f"({display_week_start_date}週) {related_project.name}に '{hours}' 稼働時間を登録しました。\n"),
                                },
                            }
                        ]
                        command.is_valid = True
                        command.save()
                    else:
                        logger.warning(
                            f"ProjectWeeklyEffort already exists for project '{related_project.name}', "
                            f"user '{command.user.username}', week_start '{week_start_date}'"
                        )
                        # ProjectWeeklyEffort already exists, return error message
                        command_response_blocks = [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"> {text_without_subcommand}\n"
                                        f"({display_week_start_date}週) {related_project.name} ({existing_effort.hours}) "
                                        f"稼働時間はすでに登録されています。"
                                    ),
                                },
                            }
                        ]

            else:
                logger.warning(f"Unable to extract hours from text with pattern ({pattern}): {text_without_subcommand}")
                # unable to extract hours from 'text_without_subcommand', return error message
                organization_slack_command = command.organization.slack_command_name
                command_response_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"> {text_without_subcommand}\n"
                                f"稼働時間を取得できませんでした。 "
                                f"`/{organization_slack_command} {cls.DISPLAY_COMMAND_NAME} HOURS` 整数で稼働時間を指定してください。"
                            ),
                        },
                    }
                ]

        webhook_send_response = None
        if command_response_blocks:
            # Notify user that notification was sent to the registered channel
            webhook_client = WebhookClient(command.response_url)
            webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)
        else:
            logger.warning(f"command_response_blocks is empty, no response sent to {command.user.username}.")
        return command_response_blocks, web_send_response, webhook_send_response
