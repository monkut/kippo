import datetime
import logging
from collections import defaultdict

from accounts.models import KippoOrganization
from django.conf import settings
from django.db.models import QuerySet
from django.utils.text import gettext_lazy as _
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..functions import current_week_startdate
from ..models import ActiveKippoProject

logger = logging.getLogger(__name__)


NULL_COMMENT = _("なし")


class ProjectSlackManager:
    def __init__(self, organization: KippoOrganization) -> None:
        self.organization = organization
        if not self.organization.enable_slack_channel_reporting:
            raise ValueError("Slack channel reporting ('enable_slack_channel_reporting') is not enabled for this organization.")
        self.client = WebClient(token=organization.slack_api_token)

    def __get_project_progress_emoji(self, project: ActiveKippoProject) -> str:
        """Calculate the project progress emoji based on actual and expected effort hours."""
        actual_effort_hours, allocated_effort_hours, total_effort_percentage = project.get_projecteffort_values()
        _, expected_effort_hours = project.get_expected_effort()
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
        return project_progress_emoji

    def _prepare_project_status_blocks(self, project: ActiveKippoProject, user_comments: dict[str, list[str]]) -> list[dict]:
        slack_status_message_blocks = []
        divider_block = {"type": "divider"}
        # PROJECT_NAME 完了予定 YYYY-MM-DD
        # :large_green_circle: 0/10h (0%)
        # ---
        # USERNAME:
        # :memo: COMMENT
        # ---
        project_progress_emoji = self.__get_project_progress_emoji(project=project)

        project_header_block = {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": (
                    f"\n\n{project.name}\n{project.start_date} - {project.target_date}\n"
                    f"{project_progress_emoji} {project.get_projecteffort_display()}"
                ),
            },
        }
        slack_status_message_blocks.append(project_header_block)
        slack_status_message_blocks.append(divider_block)
        if user_comments:
            # prepare user comments block
            for user_display_name, comments in user_comments.items():
                logger.debug(f"user_display_name={user_display_name}, comments={comments}")
                user_comments_block = {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (f"*{user_display_name}*\n:memo:{'\n'.join(str(c) for c in comments)}\n"),
                    },
                }
                slack_status_message_blocks.append(user_comments_block)
            slack_status_message_blocks.append(divider_block)
        else:
            logger.warning(f"No comments found for project {project.name}")
        return slack_status_message_blocks

    def get_active_kippoprojects(self) -> QuerySet[ActiveKippoProject]:
        """Get active KippoProjects for the organization."""
        # Get the list of projects for the organization
        contract_complete_confidence = 100
        projects = ActiveKippoProject.objects.filter(
            organization=self.organization,
            confidence=contract_complete_confidence,
            display_in_project_report=True,
        ).order_by("target_date", "name")
        logger.debug(f"Found {self.organization.github_organization_name} projects: {len(projects)}")
        return projects

    @staticmethod
    def _get_project_user_comments(week_start_datetime: datetime.datetime, project: ActiveKippoProject) -> dict[str, list[str]]:
        user_comments = defaultdict(list)

        # collect user comments for the week
        # - results ordered by user/username
        for status_entry in project.get_weekly_kippoprojectstatus_entries(week_start_datetime=week_start_datetime):
            if status_entry and status_entry.created_by:
                user_comments[status_entry.created_by.display_name].append(status_entry.comment.strip())
            else:
                logger.warning(
                    f"KippoProjectStatus without created_by foundL: "
                    f"project={project.name}, "
                    f"status_entry.pk={status_entry.pk}, "
                    f"status_entry={status_entry}"
                )

        if not user_comments:
            logger.warning(
                f"No comments found for project {project.name}, setting NULL_COMMENT({NULL_COMMENT}): week_start_datetime={week_start_datetime}"
            )
            # NULL_COMMENT commentを追加
            user_comments["-"].append(NULL_COMMENT)
        return user_comments

    def _build_weekly_project_status_blocks(self, week_start_datetime: datetime.datetime | None = None) -> list[list[dict]]:
        if not week_start_datetime:
            week_start_date = current_week_startdate()
            time_deadline = self.organization.weekly_project_time_deadline
            week_start_datetime = datetime.datetime.combine(week_start_date, time_deadline, tzinfo=settings.JST)

        projects: QuerySet[ActiveKippoProject] = self.get_active_kippoprojects()

        slack_max_blocks_per_message = 50
        project_status_block_groups = []
        slack_status_message_blocks = []
        for project in projects:
            user_comments = self._get_project_user_comments(week_start_datetime=week_start_datetime, project=project)

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
            blocks = self._prepare_project_status_blocks(project=project, user_comments=user_comments)
            slack_status_message_blocks.extend(blocks)

        if slack_status_message_blocks:
            # add the last message block group
            project_status_block_groups.append(slack_status_message_blocks)
        return project_status_block_groups

    def post_weekly_project_status(self, week_start_datetime: datetime.datetime | None = None) -> tuple[list[list[dict]], list[dict]]:
        """Post the weekly project status to the Slack channel."""
        project_status_block_groups = self._build_weekly_project_status_blocks(week_start_datetime=week_start_datetime)

        responses = []
        for group_message_blocks in project_status_block_groups:
            try:
                response = self.client.chat_postMessage(channel=self.organization.slack_weekly_project_report_channel, blocks=group_message_blocks)
                responses.append(response)
            except SlackApiError as e:
                logger.exception(f"{e.response.status_code} {e.response['error']}")

        return project_status_block_groups, responses
