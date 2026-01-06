import logging
from collections import defaultdict

from django.utils import timezone

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
        lambda: {
            "cumulative": 0.0,
            "current_month_cost": 0.0,
            "current_month_itemized_cost": None,
        }
    )

    # Get current month (first day of month for comparison)
    today = timezone.now().date()
    current_month_start = today.replace(day=1)

    for monthly_cost in ProjectMonthlyCost.objects.filter(project__in=projects):
        monthlycost_by_project[monthly_cost.project]["cumulative"] += monthly_cost.cost
        if monthly_cost.month == current_month_start:
            monthlycost_by_project[monthly_cost.project]["current_month_cost"] = monthly_cost.cost
            monthlycost_by_project[monthly_cost.project]["current_month_itemized_cost"] = monthly_cost.itemized_cost

    dispatched_projects = []
    current_month_str = current_month_start.isoformat()
    for project in projects:
        cost_data = monthlycost_by_project[project]
        # send_project_cost_report is decorated with @task, returns LambdaAsyncResponse (not JSON serializable)
        send_project_cost_report(
            project_id=str(project.pk),
            cumulative_cost=cost_data["cumulative"],
            current_month_cost=cost_data["current_month_cost"],
            current_month=current_month_str,
            current_month_itemized_cost=cost_data["current_month_itemized_cost"],
        )
        dispatched_projects.append({"project_id": str(project.pk), "project_name": project.name})
    return dispatched_projects
