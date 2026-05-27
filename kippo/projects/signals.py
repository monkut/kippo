"""Signal handlers for the projects app.

Currently wires:

- `post_save` on `ProjectMonthlyAssignment` → auto-create future-month rows when
  any newly-confirmed row exists at or after `start_month` and a contiguous run
  of fully-confirmed months extends from `start_month` (kippo#17).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from projects.models import ProjectMonthlyAssignment
from projects.services.autoassign import auto_create_future_assignments, latest_contiguous_confirmed_month

if TYPE_CHECKING:
    from accounts.models import KippoUser


@receiver(post_save, sender=ProjectMonthlyAssignment)
def handle_assignment_confirmation(  # noqa: D401 — Django signal handler
    sender: type[ProjectMonthlyAssignment],  # noqa: ARG001
    instance: ProjectMonthlyAssignment,
    created: bool,  # noqa: ARG001 — we react to confirmations, not pure creates
    **kwargs,  # noqa: ARG001 — Django signal kwargs (raw, using, update_fields, etc.)
) -> None:
    """Trigger auto-create of future-month rows on confirmation of any row whose
    `month >= project.start_month`, provided `start_month` itself is fully confirmed.

    Defers execution via `transaction.on_commit` so the new rows are only persisted after
    the originating save commits — avoids dangling future rows when the trigger save rolls
    back. The auto-create routine itself is idempotent (it only persists MISSING months
    inside `(latest_confirmed_month, target_month]`).
    """
    if not instance.is_confirmed:
        return
    project = instance.project
    if project.start_date is None:
        return
    start_month = project.start_date.replace(day=1)
    if instance.month < start_month:
        return
    latest_confirmed = latest_contiguous_confirmed_month(project)
    if latest_confirmed is None:
        return

    triggered_by: KippoUser | None = instance.updated_by or instance.created_by
    transaction.on_commit(lambda: auto_create_future_assignments(project, triggered_by))
