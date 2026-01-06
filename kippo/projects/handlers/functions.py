import logging
from collections import defaultdict

from ..models import KippoProject, ProjectMonthlyCost

logger = logging.getLogger(__name__)


def run_weeklyprojectstatus(event: dict | None, context: dict | None) -> list:  # noqa: ARG001
    """Run weekly project status."""
    from accounts.models import KippoOrganization

    from projects.slackcommand.managers import ProjectSlackManager

    organizations_with_reporting_enabled = KippoOrganization.objects.filter(enable_slack_channel_reporting=True)

    logger.info(f"len(organizations_with_reporting_enabled)={len(organizations_with_reporting_enabled)}")
    responses = []
    all_status_groups = []
    for organization in organizations_with_reporting_enabled:
        logger.info(f"Calling ProjectSlackManager.post_weekly_project_status() for ({organization.name}) ...")
        mgr = ProjectSlackManager(organization=organization)
        block_groups, web_client_response = mgr.post_weekly_project_status()
        all_status_groups.extend(block_groups)
        responses.append(web_client_response)
        logger.info(f"Calling ProjectSlackManager.post_weekly_project_status() for ({organization.name}) ... DONE")
    return all_status_groups


def run_project_cost_reports(event: dict | None, context: dict | None) -> list:  # noqa: ARG001
    """Check KippoProject.enable_cost_report value, if true, prepare 'slack' cost report and post to registered channel."""
    from ..reports.functions import send_project_cost_report

    projects = KippoProject.objects.filter(enable_cost_report=True)
    monthlycost_by_project: dict[KippoProject, dict] = defaultdict(
        lambda: {"cumulative": 0.0, "latest_monthly_cost": 0.0, "latest_month": None, "latest_itemized_cost": None}
    )

    for monthly_cost in ProjectMonthlyCost.objects.filter(project__in=projects).order_by("month"):
        monthlycost_by_project[monthly_cost.project]["cumulative"] += monthly_cost.cost
        # ordered by month, so last entry will be the latest
        monthlycost_by_project[monthly_cost.project]["latest_monthly_cost"] = monthly_cost.cost
        monthlycost_by_project[monthly_cost.project]["latest_month"] = monthly_cost.month
        monthlycost_by_project[monthly_cost.project]["latest_itemized_cost"] = monthly_cost.itemized_cost

    responses = []
    for project in projects:
        cost_data = monthlycost_by_project[project]
        latest_month = cost_data["latest_month"]
        # Convert date to ISO string for JSON serialization (zappa async task)
        latest_month_str = latest_month.isoformat() if latest_month else None
        response = send_project_cost_report(
            project_id=str(project.pk),
            cumulative_cost=cost_data["cumulative"],
            latest_monthly_cost=cost_data["latest_monthly_cost"],
            latest_month=latest_month_str,
            latest_itemized_cost=cost_data["latest_itemized_cost"],
        )
        responses.append(response)
    return responses
