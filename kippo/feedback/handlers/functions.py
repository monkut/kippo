import logging

logger = logging.getLogger(__name__)


def run_weeklyfeedbacksummary(event: dict | None, context: dict | None) -> list:  # noqa: ARG001
    """Run weekly feedback summary across all organizations that have Slack reporting enabled."""
    from accounts.models import KippoOrganization

    from feedback.slackcommand.managers import FeedbackSlackManager

    organizations_with_reporting_enabled = KippoOrganization.objects.filter(enable_slack_channel_reporting=True)
    logger.info(f"len(organizations_with_reporting_enabled)={len(organizations_with_reporting_enabled)}")

    all_block_groups: list[list[dict]] = []
    for organization in organizations_with_reporting_enabled:
        logger.info(f"Calling FeedbackSlackManager.post_weekly_feedback_summary() for ({organization.name}) ...")
        mgr = FeedbackSlackManager(organization=organization)
        block_groups, _ = mgr.post_weekly_feedback_summary()
        all_block_groups.extend(block_groups)
        logger.info(f"Calling FeedbackSlackManager.post_weekly_feedback_summary() for ({organization.name}) ... DONE")
    return all_block_groups
