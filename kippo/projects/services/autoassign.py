"""Auto-create future-month ProjectMonthlyAssignment rows on first-month full confirmation.

When every `ProjectMonthlyAssignment` row for a project's start month is confirmed,
this service builds uncommitted (`is_confirmed=False`) rows for every subsequent
month through `project.target_date`, scaling per-user percentages so the project's
estimated completion lands on (or before) `target_date`.

See monkut/kippo#240 decisions D1–D4 for the algorithm details.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import TYPE_CHECKING

from accounts.models import KippoUser
from commons.functions import first_of_next_month
from dateutil.relativedelta import relativedelta
from django.db.models import Sum
from django.utils import timezone

from projects.exceptions import ProjectStartDateRequiredError
from projects.models import MAX_ASSIGNMENT_PERCENTAGE, ProjectMonthlyAssignment
from projects.services.forecast import ProjectAssignmentForecastManager

if TYPE_CHECKING:
    import datetime

    from projects.models import KippoProject

logger = logging.getLogger(__name__)

ON_TARGET_TOLERANCE_DAYS = 3  # D1: skip scaling when estimated_completion ≤ target_date + 3 days
SCALE_BINARY_SEARCH_ITERATIONS = 24  # D1: binary search depth — 24 halvings narrow s to ~1e-7
SCALE_FACTOR_MIN = 1.0  # never scale percentages down


def auto_create_future_assignments(project: KippoProject, triggered_by: KippoUser | None) -> list[ProjectMonthlyAssignment]:
    """Create uncommitted future-month ProjectMonthlyAssignment rows for `project`.

    Idempotent: returns `[]` and skips work if any future-month rows already exist
    for the project, if the project is closed / has an actual date / has no
    target_date / has no start_date, or if the seed shape is empty.

    `triggered_by` is the user attributed as `created_by` / `updated_by` on the new
    rows (D2). Resolved by the signal from the trigger row's `updated_by` (falling
    back to `created_by`); may be `None` only when no provenance is recoverable.
    """
    if not _is_eligible(project):
        return []

    start_month = project.start_date.replace(day=1)
    target_month = project.target_date.replace(day=1)
    months = _future_months(start_month, target_month)
    seed_pct = _compute_seed_shape(project, start_month)

    blocking_conditions = (
        ProjectMonthlyAssignment.objects.filter(project=project, month__gt=start_month).exists(),
        not seed_pct,
        not months,
    )
    if any(blocking_conditions):
        return []

    try:
        scaled_pct = _scaled_percentages(project, seed_pct, months)
    except ProjectStartDateRequiredError:
        logger.warning("project %s: forecast raised ProjectStartDateRequiredError — skipping auto-create", project.pk)
        return []
    except Exception:
        logger.exception("project %s: forecast raised unexpectedly — skipping auto-create", project.pk)
        return []

    return _persist(project, scaled_pct, months, triggered_by)


def _is_eligible(project: KippoProject) -> bool:
    if project.is_closed:
        return False
    if project.actual_date is not None:
        return False
    if project.start_date is None or project.target_date is None:
        return False
    start_month = project.start_date.replace(day=1)
    return project.target_date > start_month


def _compute_seed_shape(project: KippoProject, start_month: datetime.date) -> dict[int, int]:
    """Per-user total percentage on the project's start month (D1 seed shape).

    Sums across rows for the same `(user, start_month)` cell — typically one row per
    user per month, but `Sum` guards against double-rows. Users with `percentage=0` are
    excluded so they don't carry forward as zero-rows.
    """
    rows = (
        ProjectMonthlyAssignment.objects.filter(project=project, month=start_month, is_confirmed=True, percentage__gt=0)
        .values("user_id")
        .annotate(total=Sum("percentage"))
    )
    return {row["user_id"]: row["total"] for row in rows if row["total"]}


def _future_months(start_month: datetime.date, target_month: datetime.date) -> list[datetime.date]:
    """Months from start_month + 1 through target_month inclusive (D3 — generate literally,
    including past months when start_date is historical).
    """
    months: list[datetime.date] = []
    current = start_month + relativedelta(months=1)
    while current <= target_month:
        months.append(current)
        current += relativedelta(months=1)
    return months


def _scaled_percentages(
    project: KippoProject,
    seed_pct: dict[int, int],
    months: list[datetime.date],
) -> dict[int, int]:
    """Return `{user_id: integer_pct}` after binary-search scaling + caps (D1).

    Scaling preserves the per-user *ratio* from the seed shape. The scaled value lands
    on each future month identically (flat across `months`). Caps are applied per-user
    against (a) the org's `project_assignment_member_soft_ceiling` and (b) the absolute
    `MAX_ASSIGNMENT_PERCENTAGE` after subtracting other-project commitments.
    """
    forecast_manager = ProjectAssignmentForecastManager(project)
    target_date = project.target_date

    # Skip scaling when the forecast has nothing meaningful to evaluate: no allocated_hours,
    # or every overlay month is filtered out by forecast.py:106 (next_month_start cutoff).
    next_month_start = first_of_next_month(timezone.localdate())
    if project.allocated_effort_hours is None or all(month < next_month_start for month in months):
        logger.info(
            "project %s: forecast cannot evaluate scaling (no allocated_hours or all months in past) — persisting seed shape",
            project.pk,
        )
        return _apply_caps(project, seed_pct, months)

    # Tolerance check at seed (no-scale path).
    seed_completion = forecast_manager.compute(overlay=_overlay(seed_pct, months)).estimated_completion_date
    if _within_tolerance(seed_completion, target_date):
        return _apply_caps(project, seed_pct, months)

    s_max = _maximum_feasible_factor(project, seed_pct, months)
    if s_max <= SCALE_FACTOR_MIN:
        # Already at cap; ceil-rounded seed is the best we can do.
        logger.warning("project %s: seed shape already at cap; cannot scale up to meet target_date", project.pk)
        return _apply_caps(project, seed_pct, months)

    factor = _binary_search_factor(forecast_manager, seed_pct, months, target_date, s_max)
    scaled = {user_id: math.ceil(pct * factor) for user_id, pct in seed_pct.items()}
    capped = _apply_caps(project, scaled, months)

    final_completion = forecast_manager.compute(overlay=_overlay(capped, months)).estimated_completion_date
    if final_completion is None or final_completion > target_date:
        logger.warning(
            "project %s: scaled team cannot land on target_date (estimated=%s, target=%s) — persisting at cap",
            project.pk,
            final_completion,
            target_date,
        )
    return capped


def _overlay(pct_per_user: dict[int, int], months: list[datetime.date]) -> dict[int, dict[datetime.date, int]]:
    """Build the forecast overlay shape `{user_id: {month: pct}}` from a flat per-user map."""
    return {user_id: dict.fromkeys(months, pct) for user_id, pct in pct_per_user.items() if pct > 0}


def _within_tolerance(estimated: datetime.date | None, target: datetime.date) -> bool:
    """D1: estimated completion is "good enough" when ≤ target_date + tolerance."""
    if estimated is None:
        return False
    return (estimated - target).days <= ON_TARGET_TOLERANCE_DAYS


def _maximum_feasible_factor(project: KippoProject, seed_pct: dict[int, int], months: list[datetime.date]) -> float:
    """Largest scale factor that keeps every user under both the org soft ceiling AND
    `MAX_ASSIGNMENT_PERCENTAGE − Σ(other org assignments)` for every month in `months`.

    The binary search uses this as its upper bound so it never proposes a factor that
    will be clipped by `_apply_caps` afterwards.
    """
    soft_ceiling = project.organization.project_assignment_member_soft_ceiling
    other_load = _other_org_load(project, list(seed_pct.keys()), months)

    factor_min = float("inf")
    for user_id, seed in seed_pct.items():
        if seed <= 0:
            continue
        per_user_cap = soft_ceiling
        for month in months:
            available = MAX_ASSIGNMENT_PERCENTAGE - other_load.get(user_id, {}).get(month, 0)
            per_user_cap = min(per_user_cap, available)
        if per_user_cap <= 0:
            return SCALE_FACTOR_MIN
        factor_min = min(factor_min, per_user_cap / seed)

    return max(SCALE_FACTOR_MIN, factor_min if factor_min != float("inf") else SCALE_FACTOR_MIN)


def _binary_search_factor(
    forecast_manager: ProjectAssignmentForecastManager,
    seed_pct: dict[int, int],
    months: list[datetime.date],
    target_date: datetime.date,
    s_max: float,
) -> float:
    """Binary-search the smallest `s ∈ [1.0, s_max]` such that estimated_completion ≤ target_date.

    The forecast is a step function over discrete days; the search converges on the
    factor that places `estimated_completion ≤ target_date` even though the mapping
    isn't continuous. Returns `s_max` if even the upper bound can't meet the target.
    """
    s_max_overlay = _overlay({user_id: math.ceil(pct * s_max) for user_id, pct in seed_pct.items()}, months)
    s_max_completion = forecast_manager.compute(overlay=s_max_overlay).estimated_completion_date
    if s_max_completion is None or s_max_completion > target_date:
        return s_max  # caller will warn — best effort.

    lo, hi = SCALE_FACTOR_MIN, s_max
    for _ in range(SCALE_BINARY_SEARCH_ITERATIONS):
        mid = (lo + hi) / 2.0
        overlay = _overlay({user_id: math.ceil(pct * mid) for user_id, pct in seed_pct.items()}, months)
        completion = forecast_manager.compute(overlay=overlay).estimated_completion_date
        if completion is not None and completion <= target_date:
            hi = mid
        else:
            lo = mid
    return hi


def _apply_caps(project: KippoProject, pct_per_user: dict[int, int], months: list[datetime.date]) -> dict[int, int]:
    """Cap each user's percentage at min(soft_ceiling, MAX − other_org_load) across all months.

    Per #240 D1, scaling overflow is "redistributed proportionally to remaining seed members"
    only when the scaling step hits a cap. In practice the binary-search upper bound (`s_max`)
    already keeps every user under the per-month cap, so this function is a defensive clamp:
    it ensures we never persist a row that would breach the cap, and logs when clipping fires.
    """
    soft_ceiling = project.organization.project_assignment_member_soft_ceiling
    other_load = _other_org_load(project, list(pct_per_user.keys()), months)

    capped: dict[int, int] = {}
    for user_id, pct in pct_per_user.items():
        ceiling = soft_ceiling
        for month in months:
            available = MAX_ASSIGNMENT_PERCENTAGE - other_load.get(user_id, {}).get(month, 0)
            ceiling = min(ceiling, available)
        ceiling = max(ceiling, 0)
        clipped = min(pct, ceiling)
        if clipped < pct:
            logger.warning(
                "project %s: user %s scaled percentage %d clipped to %d (soft_ceiling=%d)",
                project.pk,
                user_id,
                pct,
                clipped,
                soft_ceiling,
            )
        capped[user_id] = clipped
    return capped


def _other_org_load(
    project: KippoProject,
    user_ids: list[int],
    months: list[datetime.date],
) -> dict[int, dict[datetime.date, int]]:
    """Sum each user's existing percentage across other projects in the same org for each month.

    Excludes `project` itself — auto-created rows are about *this* project's allocation,
    so the cap headroom is "100% minus what you've already promised elsewhere".
    """
    if not user_ids or not months:
        return {}
    rows = (
        ProjectMonthlyAssignment.objects.filter(
            project__organization=project.organization,
            user_id__in=user_ids,
            month__in=months,
        )
        .exclude(project=project)
        .values("user_id", "month")
        .annotate(total=Sum("percentage"))
    )
    out: dict[int, dict[datetime.date, int]] = defaultdict(dict)
    for row in rows:
        out[row["user_id"]][row["month"]] = row["total"] or 0
    return out


def _persist(
    project: KippoProject,
    pct_per_user: dict[int, int],
    months: list[datetime.date],
    triggered_by: KippoUser | None,
) -> list[ProjectMonthlyAssignment]:
    """D4: bulk_create the future rows in a single INSERT, skipping post_save + full_clean.

    Drops users whose final per-user percentage clipped to 0 — persisting a 0% row would
    look like an explicit "this user is on the project at zero" signal, which is not
    what auto-create means.
    """
    valid_user_ids = [user_id for user_id, pct in pct_per_user.items() if pct > 0]
    if not valid_user_ids:
        return []

    user_lookup = {u.id: u for u in KippoUser.objects.filter(id__in=valid_user_ids)}
    rows: list[ProjectMonthlyAssignment] = []
    for user_id in valid_user_ids:
        user = user_lookup.get(user_id)
        if user is None:
            continue
        pct = pct_per_user[user_id]
        for month in months:
            rows.append(
                ProjectMonthlyAssignment(
                    project=project,
                    user=user,
                    month=month,
                    is_confirmed=False,
                    percentage=pct,
                    created_by=triggered_by,
                    updated_by=triggered_by,
                )
            )
    return ProjectMonthlyAssignment.objects.bulk_create(rows)


def all_first_month_rows_confirmed(project: KippoProject) -> bool:
    """Return True iff at least one row exists for the project's start month and every
    such row has `is_confirmed=True`. Used by the post_save signal as the trigger guard.
    """
    if project.start_date is None:
        return False
    start_month = project.start_date.replace(day=1)
    rows = ProjectMonthlyAssignment.objects.filter(project=project, month=start_month)
    if not rows.exists():
        return False
    return not rows.filter(is_confirmed=False).exists()
