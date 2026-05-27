"""Auto-create future-month ProjectMonthlyAssignment rows on confirmation.

When any `ProjectMonthlyAssignment` row for a project becomes confirmed and the
project has a contiguous run of fully-confirmed months from `start_month` onward,
this service builds uncommitted (`is_confirmed=False`) rows for every *missing*
month between `(latest_confirmed_month, target_date]`, scaling per-user
percentages so the project's estimated completion lands on (or before)
`target_date`.

See kiconiaworks/kippo#17 for the trigger-relaxation + top-up semantics
(supersedes the original "only on start_month full confirmation" trigger).
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

from projects.definitions import SkipReason
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


def auto_create_future_assignments(  # noqa: PLR0911
    project: KippoProject, triggered_by: KippoUser | None
) -> tuple[list[ProjectMonthlyAssignment], SkipReason | None]:
    """Create uncommitted future-month ProjectMonthlyAssignment rows for `project`.

    Top-up semantics (kippo#17): given a contiguous run of fully-confirmed months
    starting at `start_month`, this only persists rows for months that are MISSING
    inside `(latest_confirmed_month, target_month]`. Existing rows (confirmed or
    unconfirmed) are never touched.

    Returns `(created_rows, skip_reason)` (kippo#19). On success `skip_reason` is `None`
    and `created_rows` holds the newly-persisted rows. On no-op, `created_rows` is `[]`
    and `skip_reason` carries a structured `SkipReason` enum value the caller (REST
    endpoint / admin action / signal log) can present to the operator.

    `triggered_by` is the user attributed as `created_by` / `updated_by` on the new
    rows (D2). Resolved by the signal from the trigger row's `updated_by` (falling
    back to `created_by`); may be `None` only when no provenance is recoverable.
    """
    skip_reason = _eligibility_skip_reason(project)
    if skip_reason is not None:
        return [], skip_reason

    latest_confirmed_month = latest_contiguous_confirmed_month(project)
    if latest_confirmed_month is None:
        return [], SkipReason.NOT_CONFIRMED

    seed_pct = _compute_seed_shape(project, latest_confirmed_month)
    if not seed_pct:
        return [], SkipReason.NO_SEED_SHAPE

    target_month = project.target_date.replace(day=1)
    candidate_months = _future_months(latest_confirmed_month, target_month)
    if not candidate_months:
        return [], SkipReason.ALREADY_COMPLETE

    # Top-up: only persist months that do not already have any row for this project.
    existing_months = set(ProjectMonthlyAssignment.objects.filter(project=project, month__in=candidate_months).values_list("month", flat=True))
    missing_months = [m for m in candidate_months if m not in existing_months]
    if not missing_months:
        return [], SkipReason.NO_MISSING_MONTHS

    try:
        scaled_pct = _scaled_percentages(project, seed_pct, missing_months)
    except ProjectStartDateRequiredError:
        logger.info(
            "auto_extend skipped: forecast raised ProjectStartDateRequiredError",
            extra={
                "project_id": str(project.pk),
                "skip_reason": SkipReason.FORECAST_UNAVAILABLE.value,
                "triggered_by_user_id": str(triggered_by.pk) if triggered_by else None,
                "latest_confirmed_month": latest_confirmed_month.isoformat(),
            },
        )
        return [], SkipReason.FORECAST_UNAVAILABLE
    except Exception:
        logger.exception(
            "auto_extend skipped: forecast raised unexpectedly",
            extra={
                "project_id": str(project.pk),
                "skip_reason": SkipReason.FORECAST_UNAVAILABLE.value,
                "triggered_by_user_id": str(triggered_by.pk) if triggered_by else None,
                "latest_confirmed_month": latest_confirmed_month.isoformat(),
            },
        )
        return [], SkipReason.FORECAST_UNAVAILABLE

    created = _persist(project, scaled_pct, missing_months, triggered_by)
    if not created:
        return [], SkipReason.NO_SEED_SHAPE
    return created, None


def _eligibility_skip_reason(project: KippoProject) -> SkipReason | None:
    """Map project state to a `SkipReason` (or None when eligible)."""
    if project.is_closed or project.actual_date is not None:
        return SkipReason.PROJECT_CLOSED
    if project.start_date is None:
        return SkipReason.MISSING_START_DATE
    if project.target_date is None:
        return SkipReason.MISSING_TARGET_DATE
    if project.target_date <= project.start_date.replace(day=1):
        return SkipReason.ALREADY_COMPLETE
    return None


def _compute_seed_shape(project: KippoProject, seed_month: datetime.date) -> dict:
    """Per-user total percentage on `seed_month` (D1 seed shape).

    Sums across rows for the same `(user, seed_month)` cell — typically one row per
    user per month, but `Sum` guards against double-rows. Users with `percentage=0` are
    excluded so they don't carry forward as zero-rows.
    """
    rows = (
        ProjectMonthlyAssignment.objects.filter(project=project, month=seed_month, is_confirmed=True, percentage__gt=0)
        .values("user_id")
        .annotate(total=Sum("percentage"))
    )
    return {row["user_id"]: row["total"] for row in rows if row["total"]}


def _future_months(seed_month: datetime.date, target_month: datetime.date) -> list:
    """Months from seed_month + 1 through target_month inclusive (D3 — generate literally,
    including past months when start_date is historical).
    """
    months: list = []
    current = seed_month + relativedelta(months=1)
    while current <= target_month:
        months.append(current)
        current += relativedelta(months=1)
    return months


def _scaled_percentages(
    project: KippoProject,
    seed_pct: dict,
    months: list,
) -> dict:
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


def _overlay(pct_per_user: dict, months: list) -> dict:
    """Build the forecast overlay shape `{user_id: {month: pct}}` from a flat per-user map."""
    return {user_id: dict.fromkeys(months, pct) for user_id, pct in pct_per_user.items() if pct > 0}


def _within_tolerance(estimated: datetime.date | None, target: datetime.date) -> bool:
    """D1: estimated completion is "good enough" when ≤ target_date + tolerance."""
    if estimated is None:
        return False
    return (estimated - target).days <= ON_TARGET_TOLERANCE_DAYS


def _maximum_feasible_factor(project: KippoProject, seed_pct: dict, months: list) -> float:
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
    seed_pct: dict,
    months: list,
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


def _apply_caps(project: KippoProject, pct_per_user: dict, months: list) -> dict:
    """Cap each user's percentage at min(soft_ceiling, MAX − other_org_load) across all months.

    Per #240 D1, scaling overflow is "redistributed proportionally to remaining seed members"
    only when the scaling step hits a cap. In practice the binary-search upper bound (`s_max`)
    already keeps every user under the per-month cap, so this function is a defensive clamp:
    it ensures we never persist a row that would breach the cap, and logs when clipping fires.
    """
    soft_ceiling = project.organization.project_assignment_member_soft_ceiling
    other_load = _other_org_load(project, list(pct_per_user.keys()), months)

    capped: dict = {}
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
    user_ids: list,
    months: list,
) -> dict:
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
    out: dict = defaultdict(dict)
    for row in rows:
        out[row["user_id"]][row["month"]] = row["total"] or 0
    return out


def _persist(
    project: KippoProject,
    pct_per_user: dict,
    months: list,
    triggered_by: KippoUser | None,
) -> list[ProjectMonthlyAssignment]:
    """D4: bulk_create the future rows in a single INSERT, skipping post_save + full_clean.

    Drops users whose final per-user percentage clipped to 0 — persisting a 0% row would
    look like an explicit "this user is on the project at zero" signal, which is not
    what auto-create means.

    After bulk_create, re-runs the per-user / per-month >100% cap warning that
    `ProjectMonthlyAssignment.save()` would have emitted, since bulk_create bypasses
    `save()` (kippo#20).
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
    created = ProjectMonthlyAssignment.objects.bulk_create(rows)
    _emit_cap_warnings(project, created)
    return created


def _emit_cap_warnings(project: KippoProject, created_rows: list[ProjectMonthlyAssignment]) -> None:
    """Replicate the >100% cap-warning logic from `ProjectMonthlyAssignment.save()` for the
    bulk-created cohort (kippo#20).

    Groups newly-created rows by `(user, month)`, sums each user's total org-wide percentage
    for that month (including pre-existing rows + the new ones), and emits the warning when
    total exceeds `MAX_ASSIGNMENT_PERCENTAGE`. Matches the format string at
    `models.py:1043-1049` so log consumers see consistent text across save() and auto-create.
    """
    if not created_rows:
        return
    organization = project.organization
    pairs = {(row.user_id, row.month) for row in created_rows}
    for user_id, month in pairs:
        total_percentage = (
            ProjectMonthlyAssignment.objects.filter(
                user_id=user_id,
                project__organization=organization,
                month=month,
            ).aggregate(total=Sum("percentage"))["total"]
            or 0
        )
        if total_percentage > MAX_ASSIGNMENT_PERCENTAGE:
            # Resolve the user instance lazily — only needed when the warning fires.
            user = next((row.user for row in created_rows if row.user_id == user_id), None)
            logger.warning(
                "User '%s' has total assignment of %d%% for organization '%s' in month %s (exceeds 100%%)",
                user,
                total_percentage,
                organization,
                month.strftime("%Y-%m"),
            )


def latest_contiguous_confirmed_month(project: KippoProject) -> datetime.date | None:
    """Return the latest month for which a contiguous confirmed run exists from `start_month`.

    Walks month-by-month from `project.start_date.replace(day=1)` forward, requiring at least
    one row per month and every row in that month to have `is_confirmed=True`. The walk halts
    at the first month with either no rows or at least one unconfirmed row, returning the
    previous month (or None if `start_month` itself is not fully confirmed).
    """
    if project.start_date is None:
        return None
    start_month = project.start_date.replace(day=1)
    months_with_rows = set(ProjectMonthlyAssignment.objects.filter(project=project, month__gte=start_month).values_list("month", flat=True))
    if not months_with_rows:
        return None
    unconfirmed_months = set(
        ProjectMonthlyAssignment.objects.filter(project=project, month__gte=start_month, is_confirmed=False).values_list("month", flat=True)
    )

    latest = None
    current = start_month
    while current in months_with_rows and current not in unconfirmed_months:
        latest = current
        current = current + relativedelta(months=1)
    return latest
