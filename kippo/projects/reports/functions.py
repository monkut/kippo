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
    latest_monthly_cost: float,
    latest_month: datetime.date | None,
    latest_itemized_cost: dict | None,
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

    # Format month display
    month_display = latest_month.strftime("%Y-%m") if latest_month else "-"

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
                "text": f"*Latest Month ({month_display}):*\n${latest_monthly_cost:,.2f}",
            },
        ],
    }
    blocks.append(summary_block)
    blocks.append(divider_block)

    # Itemized cost breakdown if available
    if latest_itemized_cost:
        itemized_text = "*Itemized Breakdown:*\n"
        for item_name, item_cost in latest_itemized_cost.items():
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
    latest_monthly_cost: float,
    latest_month: str | None,
    latest_itemized_cost: dict | None,
) -> dict | None:
    """Send cost report to project's Slack channel.

    This function is decorated with @task to run asynchronously in a separate Lambda.
    All arguments must be JSON serializable.

    Args:
        project_id: The KippoProject UUID as string
        cumulative_cost: Total cumulative cost across all months
        latest_monthly_cost: Cost for the latest month
        latest_month: The date of the latest month as ISO format string (YYYY-MM-DD)
        latest_itemized_cost: Itemized cost breakdown for the latest month

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

    # Parse latest_month back to date object (date has no timezone, suppress DTZ007)
    latest_month_date = datetime.datetime.strptime(latest_month, "%Y-%m-%d").date() if latest_month else None  # noqa: DTZ007

    client = WebClient(token=project.organization.slack_api_token)
    blocks = _build_cost_report_blocks(
        project=project,
        cumulative_cost=cumulative_cost,
        latest_monthly_cost=latest_monthly_cost,
        latest_month=latest_month_date,
        latest_itemized_cost=latest_itemized_cost,
    )

    response = None
    try:
        response = client.chat_postMessage(channel=project.slack_channel_name, blocks=blocks)
        logger.info(f"Cost report sent to {project.slack_channel_name} for project {project.name}")
    except SlackApiError as e:
        logger.exception(f"Failed to send cost report for {project.name}: {e.response.status_code} {e.response['error']}")

    return response
