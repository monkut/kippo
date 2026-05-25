import datetime
import logging
import re
from collections import defaultdict

from accounts.models import KippoOrganization
from django.db.models import QuerySet
from django.utils import timezone
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..models import Feedback

logger = logging.getLogger(__name__)


UNCATEGORIZED_COMPONENT = "uncategorized"
FEEDBACK_WINDOW_DAYS = 7
SLACK_MAX_BLOCKS_PER_MESSAGE = 50
COMPONENT_PREFIX_PATTERN = re.compile(r"^\s*\[([^\[\]]+)\]")


def infer_feedback_component(feedback: Feedback) -> str:
    """Extract a bracketed `[component]` prefix from the feedback title.

    Returns the trimmed component string, or `UNCATEGORIZED_COMPONENT` if no
    bracketed prefix is present.
    """
    match = COMPONENT_PREFIX_PATTERN.match(feedback.title or "")
    if not match:
        return UNCATEGORIZED_COMPONENT
    component = match.group(1).strip()
    return component or UNCATEGORIZED_COMPONENT


class FeedbackSlackManager:
    def __init__(self, organization: KippoOrganization) -> None:
        self.organization = organization
        if not self.organization.enable_slack_channel_reporting:
            raise ValueError("Slack channel reporting ('enable_slack_channel_reporting') is not enabled for this organization.")
        self.client = WebClient(token=organization.slack_api_token)

    def get_recent_feedback(self, end_datetime: datetime.datetime) -> QuerySet[Feedback]:
        """Get feedback entries created in the FEEDBACK_WINDOW_DAYS preceding `end_datetime` for the organization."""
        start_datetime = end_datetime - datetime.timedelta(days=FEEDBACK_WINDOW_DAYS)
        return Feedback.objects.filter(
            organization=self.organization,
            created_datetime__gte=start_datetime,
            created_datetime__lt=end_datetime,
        ).order_by("created_datetime")

    @staticmethod
    def _group_by_component(entries: QuerySet[Feedback]) -> dict[str, list[Feedback]]:
        grouped: dict[str, list[Feedback]] = defaultdict(list)
        for entry in entries:
            component = infer_feedback_component(entry)
            grouped[component].append(entry)
        return grouped

    @staticmethod
    def _prepare_component_blocks(component: str, entries: list[Feedback]) -> list[dict]:
        divider_block = {"type": "divider"}
        component_header_block = {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{component} ({len(entries)})",
            },
        }
        blocks: list[dict] = [component_header_block, divider_block]
        for entry in entries:
            created_by_display = entry.created_by.display_name if entry.created_by else "-"
            entry_text = (
                f"*[{entry.category}] {entry.title}*\n"
                f":memo: {entry.comment.strip()}\n"
                f"_{created_by_display} • {entry.created_datetime:%Y-%m-%d %H:%M}_"
            )
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": entry_text},
                }
            )
        blocks.append(divider_block)
        return blocks

    def _build_weekly_feedback_blocks(self, end_datetime: datetime.datetime) -> list[list[dict]]:
        start_datetime = end_datetime - datetime.timedelta(days=FEEDBACK_WINDOW_DAYS)
        entries = self.get_recent_feedback(end_datetime=end_datetime)

        summary_header_block = {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Feedback Summary: {start_datetime:%Y-%m-%d} - {end_datetime:%Y-%m-%d}",
            },
        }

        if not entries:
            return [
                [
                    summary_header_block,
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "_No feedback received in the last 7 days._"},
                    },
                ]
            ]

        grouped = self._group_by_component(entries=entries)
        block_groups: list[list[dict]] = []
        current_blocks: list[dict] = [summary_header_block, {"type": "divider"}]
        for component in sorted(grouped.keys()):
            component_entries = grouped[component]
            component_blocks = self._prepare_component_blocks(component=component, entries=component_entries)
            if len(current_blocks) + len(component_blocks) > SLACK_MAX_BLOCKS_PER_MESSAGE:
                block_groups.append(current_blocks)
                current_blocks = []
            current_blocks.extend(component_blocks)
        if current_blocks:
            block_groups.append(current_blocks)
        return block_groups

    def post_weekly_feedback_summary(self, end_datetime: datetime.datetime | None = None) -> tuple[list[list[dict]], list[dict]]:
        """Post the weekly feedback summary to the organization's Slack channel."""
        if end_datetime is None:
            end_datetime = timezone.now()
        block_groups = self._build_weekly_feedback_blocks(end_datetime=end_datetime)

        responses: list[dict] = []
        for group_message_blocks in block_groups:
            try:
                response = self.client.chat_postMessage(
                    channel=self.organization.slack_weekly_project_report_channel,
                    blocks=group_message_blocks,
                )
                responses.append(response)
            except SlackApiError as e:
                logger.exception(f"{e.response.status_code} {e.response['error']}")
        return block_groups, responses
