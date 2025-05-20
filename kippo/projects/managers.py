import datetime
import logging
from collections import defaultdict

from accounts.models import KippoOrganization
from django.conf import settings
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

    def _prepare_project_status_blocks(
        self, week_start_date: datetime.date, project: ActiveKippoProject, user_comments: dict[str, list[str]]
    ) -> list[dict]:
        slack_status_message_blocks = []
        divider_block = {"type": "divider"}
        # PROJECT_NAME 完了予定 YYYY-MM-DD
        # :large_green_circle: 0/10h (0%)
        # ---
        # USERNAME:
        # :memo: COMMENT
        # ---
        actual_effort_hours, allocated_effort_hours, total_effort_percentage = project.get_projecteffort_values()
        expected_effort_hours = project.get_expected_effort_hours()
        logger.debug(
            f"project={project.name}, allocated_effort_hours={allocated_effort_hours}, "
            f"actual_effort_hours={actual_effort_hours}, expected_effort_hours={expected_effort_hours}"
        )
        project_progress_emoji = ":white_circle:"
        if actual_effort_hours and expected_effort_hours:
            if actual_effort_hours < expected_effort_hours:
                project_progress_emoji = ":large_green_circle:"
            else:
                project_progress_emoji = ":large_yellow_circle:"
                percentage_exceeding_expected = ((actual_effort_hours - expected_effort_hours) / expected_effort_hours) * 100
                if percentage_exceeding_expected > settings.PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD:
                    project_progress_emoji = ":red_circle:"

        project_header_block = {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"\n\n{project.name}\n完了予定日: {project.target_date}\n{project_progress_emoji} {project.get_projecteffort_display()}",
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
                        "text": (f"*{user_display_name}*\n:memo:{'\n'.join(comments)}\n"),
                    },
                }
                slack_status_message_blocks.append(user_comments_block)
            slack_status_message_blocks.append(divider_block)
        else:
            logger.warning(f"No comments found for project {project.name} in week starting {week_start_date}")
        return slack_status_message_blocks

    def post_weekly_project_status(self, week_start_date: datetime.date | None = None) -> tuple[list[list[dict]], list[dict]]:
        """Post the weekly project status to the Slack channel."""
        if not week_start_date:
            week_start_date = previous_week_startdate()

        # Get the list of projects for the organization
        contract_complete_confidence = 100
        projects = ActiveKippoProject.objects.filter(organization=self.organization, confidence=contract_complete_confidence).order_by(
            "target_date", "name"
        )
        logger.debug(f"Found {self.organization.github_organization_name} projects: {len(projects)}")

        slack_max_blocks_per_message = 50
        project_status_block_groups = []
        slack_status_message_blocks = []
        for project in projects:
            user_comments = defaultdict(list)

            # collect user comments for the week
            for status_entry in project.get_weekly_kippoprojectstatus_entries(week_start_date=week_start_date):  # results ordered by user/username
                user_comments[status_entry.created_by.display_name].append(status_entry.comment.strip())

            if not user_comments:
                logger.warning(f"No *NEW* comments found for project {project.name} in week starting {week_start_date}, using latest comment")
                # get latest comment for the project
                latest_status_entry = project.get_latest_kippoprojectstatus()
                user_comments[latest_status_entry.created_by.display_name].append(latest_status_entry.comment.strip())

            logger.debug(f"project={project.name}, len(user_comments)={len(user_comments)}")

            # Check that Slack block limit (slack_max_blocks_per_message) is not exceeded
            project_header_block_count = 2  # project header + divider
            per_user_block_count = 2  # user comment + divider
            if (
                len(slack_status_message_blocks) + (len(user_comments) * per_user_block_count) + project_header_block_count
                > slack_max_blocks_per_message
            ):
                project_status_block_groups.append(slack_status_message_blocks)

                # reset the blocks for the message
                slack_status_message_blocks = []
            blocks = self._prepare_project_status_blocks(week_start_date=week_start_date, project=project, user_comments=user_comments)
            slack_status_message_blocks.extend(blocks)

        if slack_status_message_blocks:
            # add the last message block group
            project_status_block_groups.append(slack_status_message_blocks)

        responses = []
        for group_message_blocks in project_status_block_groups:
            try:
                response = self.client.chat_postMessage(channel=self.organization.slack_weekly_project_report_channel, blocks=group_message_blocks)
                responses.append(response)
            except SlackApiError as e:
                logger.exception(f"{e.response.status_code} {e.response['error']}")
                response = None

        return project_status_block_groups, responses
