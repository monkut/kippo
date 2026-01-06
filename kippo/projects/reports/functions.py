import datetime
import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from zappa.asynchronous import task

from ..models import KippoProject

logger = logging.getLogger(__name__)


def _build_cost_report_blocks(
    project: KippoProject,
    cumulative_cost: float,
    current_month_cost: float,
    current_month: datetime.date,
    current_month_itemized_cost: dict | None,
) -> list[dict]:
    """Build Slack blocks for cost report."""
    blocks = []
    divider_block = {"type": "divider"}

    # Header
    header_block = {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"💰 Cost Report: {project.name}",
        },
    }
    blocks.append(header_block)
    blocks.append(divider_block)

    current_month_display = current_month.strftime("%Y-%m")

    # Summary section
    summary_block = {
        "type": "section",
        "fields": [
            {
                "type": "mrkdwn",
                "text": f"*Cumulative Cost:*\n${cumulative_cost:,.2f}",
            },
            {
                "type": "mrkdwn",
                "text": f"*Current Month ({current_month_display}):*\n${current_month_cost:,.2f}",
            },
        ],
    }
    blocks.append(summary_block)
    blocks.append(divider_block)

    # Current month itemized breakdown if available
    if current_month_itemized_cost:
        itemized_text = f"*Itemized ({current_month_display}):*\n"
        for item_name, item_cost in current_month_itemized_cost.items():
            if isinstance(item_cost, (int, float)):
                itemized_text += f"• {item_name}: ${item_cost:,.2f}\n"
            else:
                itemized_text += f"• {item_name}: {item_cost}\n"

        itemized_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": itemized_text,
            },
        }
        blocks.append(itemized_block)
        blocks.append(divider_block)

    return blocks


@task
def send_project_cost_report(
    project_id: str,
    cumulative_cost: float,
    current_month_cost: float,
    current_month: str,
    current_month_itemized_cost: dict | None,
) -> dict | None:
    """Send cost report to project's Slack channel.

    This function is decorated with @task to run asynchronously in a separate Lambda.
    All arguments must be JSON serializable.

    Args:
        project_id: The KippoProject UUID as string
        cumulative_cost: Total cumulative cost across all months
        current_month_cost: Cost for the current calendar month
        current_month: The current month as ISO format string (YYYY-MM-DD)
        current_month_itemized_cost: Itemized cost breakdown for the current month

    Returns:
        Slack API response or None if posting failed
    """
    project = KippoProject.objects.get(pk=project_id)

    if not project.slack_channel_name:
        logger.warning(f"Project {project.name} has no slack_channel_name configured, skipping cost report")
        return None

    if not project.organization.slack_api_token:
        logger.warning(f"Organization {project.organization.name} has no slack_api_token configured, skipping cost report")
        return None

    # Parse date back to date object (date has no timezone, suppress DTZ007)
    current_month_date = datetime.datetime.strptime(current_month, "%Y-%m-%d").date()  # noqa: DTZ007

    client = WebClient(token=project.organization.slack_api_token)
    blocks = _build_cost_report_blocks(
        project=project,
        cumulative_cost=cumulative_cost,
        current_month_cost=current_month_cost,
        current_month=current_month_date,
        current_month_itemized_cost=current_month_itemized_cost,
    )

    try:
        response = client.chat_postMessage(channel=project.slack_channel_name, blocks=blocks)
        logger.info(f"Cost report sent to {project.slack_channel_name} for project {project.name}")
        # Return serializable dict instead of SlackResponse to avoid Lambda serialization errors
        return {"ok": response.get("ok"), "channel": response.get("channel"), "ts": response.get("ts")}
    except SlackApiError as e:
        logger.exception(f"Failed to send cost report for {project.name}: {e.response.status_code} {e.response['error']}")
        return None
