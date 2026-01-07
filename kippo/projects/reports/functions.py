import datetime
import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from zappa.asynchronous import task

from ..models import KippoProject

logger = logging.getLogger(__name__)


def _build_itemized_section(
    title: str,
    itemized_cost: dict[str, float],
) -> dict:
    """Build a Slack section block for itemized costs.

    Args:
        title: Section title (e.g., "Account Name" or "Total")
        itemized_cost: Dict mapping service name to cost
    """
    # Calculate account total from itemized costs
    account_total = sum(c for c in itemized_cost.values() if isinstance(c, (int, float)))

    itemized_text = f"*{title}:* ${account_total:,.2f}\n"
    # Sort by cost descending
    sorted_items = sorted(itemized_cost.items(), key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0, reverse=True)
    for item_name, item_cost in sorted_items:
        if isinstance(item_cost, (int, float)):
            itemized_text += f"  • {item_name}: ${item_cost:,.2f}\n"
        else:
            itemized_text += f"  • {item_name}: {item_cost}\n"

    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": itemized_text,
        },
    }


def _build_cost_report_blocks(
    project: KippoProject,
    cumulative_cost: float,
    current_month_cost: float,
    current_month: datetime.date,
    current_month_itemized_cost: dict | None,
) -> list[dict]:
    """Build Slack blocks for cost report.

    Handles both flat itemized_cost (legacy: {"service": cost}) and
    nested per-account itemized_cost ({"account_name": {"service": cost}, "total": {...}}).
    """
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
        # Check if this is nested per-account format (has 'total' key with dict value)
        has_total_key = "total" in current_month_itemized_cost
        total_value = current_month_itemized_cost.get("total")
        is_nested_format = has_total_key and isinstance(total_value, dict)

        if is_nested_format:
            # New format: per-account breakdown with 'total'
            # First show per-account breakdowns (sorted by account total, descending)
            account_totals = []
            for account_name, services in current_month_itemized_cost.items():
                if account_name == "total" or not isinstance(services, dict):
                    continue
                account_sum = sum(c for c in services.values() if isinstance(c, (int, float)))
                account_totals.append((account_name, services, account_sum))

            # Sort accounts by total cost descending
            account_totals.sort(key=lambda x: x[2], reverse=True)

            for account_name, services, _ in account_totals:
                blocks.append(_build_itemized_section(account_name, services))

            # Add divider before total if we have account breakdowns
            if account_totals:
                blocks.append(divider_block)

            # Show summed total
            blocks.append(_build_itemized_section(f"Total ({current_month_display})", total_value))
        else:
            # Legacy flat format: {"service": cost}
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
