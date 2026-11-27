"""Default per-role daily rates seeded onto a new project."""

import json
import logging
from pathlib import Path

from django.conf import settings

from ..definitions import (
    ProjectRoles,
)

logger = logging.getLogger(__name__)

# Default per-role daily rates seeded onto a new project at registration (save_model).
# Edit the values in fixtures/default_projectassignmentrates.json — no code change needed.
# Keep the roles in this file aligned with ProjectRoles (rows with an unknown role are skipped).
DEFAULT_ASSIGNMENT_RATES_FIXTURE = Path(__file__).parent.parent / "fixtures" / "default_projectassignmentrates.json"


def _default_assignment_rate_initial() -> tuple[dict, ...]:
    """Role/rate_per_day defaults seeded onto a new project at registration (save_model).

    Read fresh each call (tiny file, only on create). Rows whose role is not a valid ProjectRoles
    value are skipped (logged); a missing/blank rate falls back to settings.DEFAULT_PROJECT_DAILY_RATE
    so the fixture and the model default cannot silently drift.
    """
    try:
        rows = json.loads(DEFAULT_ASSIGNMENT_RATES_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Failed to load default project assignment rates fixture: %s", DEFAULT_ASSIGNMENT_RATES_FIXTURE)
        return ()
    valid_roles = set(ProjectRoles.values())
    initial = []
    for row in rows:
        role = row.get("role")
        if role not in valid_roles:
            logger.warning("Skipping default assignment rate with unknown role %r (valid roles: %s)", role, sorted(valid_roles))
            continue
        initial.append({"role": role, "rate_per_day": row.get("rate_per_day") or settings.DEFAULT_PROJECT_DAILY_RATE})
    return tuple(initial)
