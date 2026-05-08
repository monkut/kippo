"""Signal handlers for the projects app.

Currently wires:

- `post_save` on `ProjectMonthlyAssignment` → auto-create future-month rows when
  every first-month row for the project becomes confirmed (kippo#240).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from projects.models import ProjectMonthlyAssignment
from projects.services.autoassign import all_first_month_rows_confirmed, auto_create_future_assignments

if TYPE_CHECKING:
    from accounts.models import KippoUser

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ProjectMonthlyAssignment)
def handle_first_month_full_confirmation(  # noqa: D401 — Django signal handler
    sender: type[ProjectMonthlyAssignment],  # noqa: ARG001
    instance: ProjectMonthlyAssignment,
    created: bool,  # noqa: ARG001 — we react to confirmations, not pure creates
    **kwargs,  # noqa: ARG001 — Django signal kwargs (raw, using, update_fields, etc.)
) -> None:
    """Trigger auto-create of future-month rows when the just-saved row's project is now
    fully confirmed for its first month and has no future-month rows yet.

    Defers execution via `transaction.on_commit` so the new rows are only persisted after
    the originating save commits — avoids dangling future rows when the trigger save rolls
    back. The auto-create routine itself is idempotent (it re-checks future-row presence
    before persisting), but the on_commit deferral keeps the signal cleanly transactional.
    """
    if not instance.is_confirmed:
        return
    project = instance.project
    if not all_first_month_rows_confirmed(project):
        return

    triggered_by: KippoUser | None = instance.updated_by or instance.created_by

    def handle_commit() -> None:
        try:
            auto_create_future_assignments(project, triggered_by)
        except Exception:
            # Defensive: a failure in auto-create must never bubble up — the originating
            # save has already committed by the time on_commit fires, but we still don't
            # want test runs / admin saves to surface unrelated stack traces.
            logger.exception("auto_create_future_assignments raised for project %s", project.pk)

    transaction.on_commit(handle_commit)
