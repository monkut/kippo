"""Functions for requirements scheduling and estimation."""

import datetime
import logging
from math import ceil
from typing import TYPE_CHECKING

from django.utils import timezone
from qlu.core import QluMilestone, QluTask, QluTaskEstimates, QluTaskScheduler

from .models import ProjectBusinessRequirementEstimate, ProjectTechnicalRequirement

if TYPE_CHECKING:
    from projects.models import KippoProject

logger = logging.getLogger(__name__)

DEFAULT_WORKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
DEFAULT_MINIMUM_ESTIMATE = 1
MAXIMUM_ESTIMATE_MULTIPLIER = 1.7


class NoTechnicalRequirementsError(Exception):
    """Raised when no technical requirements with estimates are found."""


class NoEstimatesError(Exception):
    """Raised when technical requirements exist but have no estimates."""


def schedule_technical_requirements(  # noqa: PLR0915
    project: "KippoProject",
    developer_count: int,
    start_date: datetime.date | None = None,
    workdays: list[str] | None = None,
) -> dict:
    """
    Schedule technical requirements for a project using QluTaskScheduler.

    Uses the technical requirement estimates to create QluTasks and schedules
    them across the specified number of developers.

    :param project: The KippoProject to schedule requirements for
    :param developer_count: Number of developers available for the work
    :param start_date: Schedule start date (defaults to today)
    :param workdays: List of workday names (defaults to Mon-Fri)
    :return: Dictionary containing schedule results:
        - estimated_completion_date: The date when all work is expected to complete
        - total_estimate_days: Sum of all requirement estimates
        - total_confidence_adjusted_days: Sum of confidence-adjusted estimates
        - requirements_count: Number of requirements scheduled
        - schedule_start_date: The start date used for scheduling
        - developer_count: Number of developers used
        - scheduled_requirements: List of scheduled requirement details
    """
    if not start_date:
        start_date = timezone.localdate()
    if not workdays:
        workdays = DEFAULT_WORKDAYS
    if developer_count < 1:
        raise ValueError("developer_count must be at least 1")

    # Get technical requirements with estimates for the project
    tech_requirements = ProjectTechnicalRequirement.objects.filter(project=project).select_related("projectbusinessrequirementestimate", "category")

    if not tech_requirements.exists():
        raise NoTechnicalRequirementsError(f"No technical requirements found for project: {project.name}")

    # Build QluTasks from technical requirements
    qlu_tasks = []
    scheduled_requirements = []
    total_estimate_days = 0.0
    total_confidence_adjusted_days = 0.0

    # Create virtual developer assignees
    developers = [f"developer_{i}" for i in range(developer_count)]

    # Prepare workdays for each developer
    assignee_workdays = dict.fromkeys(developers, workdays)

    # Create a project milestone for scheduling bounds
    project_start = project.start_date or start_date
    # Use a far future date if no target date, scheduler will determine actual end
    project_target = project.target_date or (start_date + datetime.timedelta(days=365 * 2))
    milestone_id = f"project-{project.id}"
    qlu_milestone = QluMilestone(milestone_id, project_start, project_target)

    requirements_with_estimates = []
    for tech_req in tech_requirements:
        try:
            estimate = tech_req.projectbusinessrequirementestimate
        except ProjectBusinessRequirementEstimate.DoesNotExist:
            # Skip requirements without estimates
            continue

        requirements_with_estimates.append((tech_req, estimate))

    if not requirements_with_estimates:
        raise NoEstimatesError(f"No estimates found for technical requirements in project: {project.name}")

    # Sort by category and then by display_id_number for consistent ordering
    requirements_with_estimates.sort(key=lambda x: (x[0].category.name, x[0].display_id_number))

    for idx, (tech_req, estimate) in enumerate(requirements_with_estimates):
        # Calculate estimates
        suggested_estimate = int(ceil(estimate.days))
        minimum_estimate = max(DEFAULT_MINIMUM_ESTIMATE, int(ceil(estimate.days * estimate.confidence)))
        maximum_estimate = int(ceil(suggested_estimate * MAXIMUM_ESTIMATE_MULTIPLIER))

        qestimates = QluTaskEstimates(minimum_estimate, suggested_estimate, maximum_estimate)

        # Round-robin assign to developers
        assignee = developers[idx % developer_count]

        # Priority based on order (lower number = higher priority)
        absolute_priority = idx + 1

        qtask = QluTask(
            tech_req.id,
            absolute_priority=absolute_priority,
            estimates=qestimates,
            assignee=assignee,
            project_id=str(project.id),
            milestone_id=milestone_id,
        )
        qlu_tasks.append(qtask)

        total_estimate_days += estimate.days
        confidence_adjusted = estimate.confidence_adjusted_days or estimate.days
        total_confidence_adjusted_days += confidence_adjusted

        scheduled_requirements.append(
            {
                "id": tech_req.id,
                "display_id": tech_req.display_id,
                "title": tech_req.title,
                "category": tech_req.category.name,
                "estimate_days": estimate.days,
                "confidence": estimate.confidence,
                "confidence_adjusted_days": confidence_adjusted,
                "assigned_developer": assignee,
                "priority": absolute_priority,
            }
        )

    # Create scheduler and schedule tasks
    scheduler = QluTaskScheduler(
        milestones=[qlu_milestone],
        holiday_dates=None,
        assignee_workdays=assignee_workdays,
        assignee_personal_holidays=None,
        start_date=start_date,
    )

    scheduled_results = scheduler.schedule(qlu_tasks)

    # Find the latest end date across all scheduled tasks
    estimated_completion_date = start_date
    for qlu_task in scheduled_results.tasks():
        if qlu_task.end_date and qlu_task.end_date > estimated_completion_date:
            estimated_completion_date = qlu_task.end_date

        # Update scheduled requirement with schedule info
        for req in scheduled_requirements:
            if req["id"] == qlu_task.id:
                req["scheduled_start_date"] = qlu_task.start_date.isoformat() if qlu_task.start_date else None
                req["scheduled_end_date"] = qlu_task.end_date.isoformat() if qlu_task.end_date else None
                req["is_scheduled"] = qlu_task.is_scheduled
                break

    return {
        "estimated_completion_date": estimated_completion_date.isoformat(),
        "total_estimate_days": total_estimate_days,
        "total_confidence_adjusted_days": total_confidence_adjusted_days,
        "requirements_count": len(scheduled_requirements),
        "schedule_start_date": start_date.isoformat(),
        "developer_count": developer_count,
        "scheduled_requirements": scheduled_requirements,
    }
