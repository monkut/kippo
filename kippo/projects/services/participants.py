"""プロジェクト参加者 services (kiconiaworks/kippo#75).

Shared by the KippoProject admin participants inline and `POST /api/projects/{id}/participants/`.
"Current month" is `timezone.localdate()` in settings.TIME_ZONE (Asia/Tokyo).
"""

import datetime

from accounts.models import KippoUser
from commons.functions import first_of_next_month
from django.db import transaction
from django.utils import timezone

from projects.models import KippoProject, ProjectMonthlyAssignment, ProjectParticipant


def current_month() -> datetime.date:
    return timezone.localdate().replace(day=1)


def remaining_months(project: KippoProject) -> list[datetime.date]:
    """R1: the later of the current month and the start_date month, through the target_date month.

    Empty when either date is missing or the period has ended.
    """
    if project.start_date is None or project.target_date is None:
        return []
    month = max(project.start_date.replace(day=1), current_month())
    target_month = project.target_date.replace(day=1)
    months = []
    while month <= target_month:
        months.append(month)
        month = first_of_next_month(month)
    return months


def create_remaining_assignments(
    participant: ProjectParticipant,
    by_user: KippoUser | None,
    percentage: int | None = None,
    role: str | None = None,
) -> tuple[list[ProjectMonthlyAssignment], list[datetime.date]]:
    """R1: one row per remaining month; months that already have a (project, user, month) row are skipped.

    Rows use the participant's percentage/role unless overridden. Also links this user's unlinked rows on the
    project (past rows kept by an earlier X1 delete). Returns (created rows, skipped months).
    """
    project = participant.project
    ProjectMonthlyAssignment.objects.filter(project=project, user=participant.user, participant__isnull=True).update(participant=participant)
    months = remaining_months(project)
    existing_months = set(
        ProjectMonthlyAssignment.objects.filter(project=project, user=participant.user, month__in=months).values_list("month", flat=True)
    )
    created_rows = []
    with transaction.atomic():
        for month in months:
            if month in existing_months:
                continue
            row = ProjectMonthlyAssignment(
                project=project,
                user=participant.user,
                participant=participant,
                month=month,
                percentage=participant.percentage if percentage is None else percentage,
                role=participant.role if role is None else role,
                created_by=by_user,
                updated_by=by_user,
            )
            row.save()  # full_clean() → org membership + participant checks
            created_rows.append(row)
    return created_rows, sorted(existing_months)


def apply_defaults_to_remaining_months(participant: ProjectParticipant, by_user: KippoUser | None) -> int:
    """E2: overwrite percentage/role on the participant's rows from the current month on. Returns rows updated."""
    return participant.monthly_assignments.filter(month__gte=current_month()).update(
        percentage=participant.percentage,
        role=participant.role,
        updated_by=by_user,
        updated_datetime=timezone.now(),
    )
