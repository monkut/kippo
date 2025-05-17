import datetime
import logging
from collections import defaultdict

from accounts.models import KippoOrganization
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .functions import previous_week_startdate
from .models import ActiveKippoProject

logger = logging.getLogger(__name__)


class ProjectSlackManager:
    def __init__(self, organization: KippoOrganization) -> None:
        self.organization = organization
        if not self.organization.enable_slack_channel_reporting:
            raise ValueError("Slack channel reporting ('enable_slack_channel_reporting') is not enabled for this organization.")
        self.client = WebClient(token=organization.slack_api_token)

    def post_weekly_project_status(self, week_start_date: datetime.date | None = None) -> tuple[list[dict], dict | None]:
        """Post the weekly project status to the Slack channel."""
        if not week_start_date:
            week_start_date = previous_week_startdate()

        # Get the list of projects for the organization
        contract_complete_confidence = 100
        projects = ActiveKippoProject.objects.filter(organization=self.organization, confidence=contract_complete_confidence).order_by(
            "target_date", "name"
        )
        logger.debug(f"Found {self.organization.github_organization_name} projects: {len(projects)}")

        slack_status_message_blocks = []
        divider_block = {"type": "divider"}
        for project in projects:
            user_comments = defaultdict(list)

            # collect user comments for the week
            for status_entry in project.get_weekly_kippoprojectstatus_entries(week_start_date=week_start_date):  # results ordered by user/username
                user_comments[status_entry.created_by.display_name].append(status_entry.comment.strip())

            # PROJECT_NAME 4h (2.00%) 完了予定 YYYY-MM-DD
            # USERNAME:
            # COMMENT
            project_header_block = {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{project.name}* {project.get_projecteffort_display()} {project.target_date} 完了予定 ",
                },
            }
            slack_status_message_blocks.append(project_header_block)
            slack_status_message_blocks.append(divider_block)
            if user_comments:
                # prepare user comments block
                for user_display_name, comments in user_comments.items():
                    user_comments_block = {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{user_display_name}*\n {'\n'.join(comments)}\n",
                        },
                    }
                    slack_status_message_blocks.append(user_comments_block)
                slack_status_message_blocks.append(divider_block)
            else:
                logger.warning(f"No comments found for project {project.name} in week starting {week_start_date}")

        try:
            response = self.client.chat_postMessage(channel=self.organization.slack_weekly_project_report_channel, blocks=slack_status_message_blocks)
        except SlackApiError as e:
            logger.exception(f"{e.response.status_code} {e.response['error']}")
            response = None

        return slack_status_message_blocks, response
