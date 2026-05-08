"""Project assignment pattern suggester.

Generates 0–4 candidate `ProjectMonthlyAssignment` patterns for a project, ranked
by closeness to `project.target_date`. Patterns vary along a continuity gradient
(P1 max past-member reuse, P2 blend past + org pool, P3 most-available pool, P4
previous-month carry-forward). See monkut/kippo#224 decisions B1–B13 +
monkut/kippo#227 clarifications S1–S4.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.functions import first_of_next_month
from dateutil.relativedelta import relativedelta
from django.db.models import Sum
from django.utils import timezone

from projects.definitions import ProjectAssignmentPattern, ProjectAssignmentPatternConflict, ProjectAssignmentPatternMember
from projects.exceptions import ProjectStartDateRequiredError
from projects.models import ProjectMonthlyAssignment, ProjectWeeklyEffort
from projects.services.forecast import ProjectAssignmentForecastManager

if TYPE_CHECKING:
    from collections.abc import Callable

    from projects.models import KippoProject


ALLOCATION_FLOOR_PERCENTAGE = 10  # B5
SOFT_CAP_TEAM_SIZE = 3  # B6
MAX_INDIVIDUAL_PERCENTAGE = 100  # absolute hard cap — over-allocation past this is always a conflict
HORIZON_MONTHS_NO_TARGET = 24  # walk this many months when project.target_date is null
HORIZON_MONTHS_PAST_TARGET = 6  # walk this many months past target for the infeasible-flag path


class PatternId(StrEnum):
    P1_MAX_REUSE = "P1-max-reuse"
    P2_BLEND = "P2-blend"
    P3_MOST_AVAILABLE = "P3-most-available"
    P4_PREVIOUS_MONTH = "P4-previous-month"


PATTERN_LABELS: dict[PatternId, str] = {
    PatternId.P1_MAX_REUSE: "Maximum past-member reuse",
    PatternId.P2_BLEND: "Blend of past members and org pool",
    PatternId.P3_MOST_AVAILABLE: "Most-available org pool members",
    PatternId.P4_PREVIOUS_MONTH: "Previous month's allocation continued",
}


@dataclass(frozen=True)
class _PatternStrategy:
    """A pattern_id paired with the team-selection rule that produces it."""

    pattern_id: PatternId
    select: Callable[[_SelectionInputs], list[KippoUser]]


@dataclass
class _SelectionInputs:
    """Inputs available to every team-selection strategy."""

    org_pool: list[KippoUser]
    past_members: set
    capacity: dict
    all_time_hours: dict[int, int]
    member_soft_ceiling: int
    # Per-user percentage on this project for `from_month - 1 month`. Empty when
    # no prior-month rows exist (greenfield, or first month after project start).
    # Drives the P4 carry-forward strategy.
    previous_month_user_pct: dict[int, int]


def _by_capacity_then_id(user: KippoUser, capacity: dict, ceiling: int) -> tuple[int, int]:
    user_existing = capacity.get(user.id, {})
    available = ceiling if not user_existing else ceiling - max(user_existing.values())
    return (-available, str(user.id).__hash__())


def _select_p1_past_only(inputs: _SelectionInputs) -> list[KippoUser]:
    if not inputs.past_members:
        return []  # S2: greenfield project — empty selection skips this strategy
    past_pool = sorted(
        (u for u in inputs.org_pool if u.id in inputs.past_members),
        key=lambda u: (-inputs.all_time_hours.get(u.id, 0), _by_capacity_then_id(u, inputs.capacity, inputs.member_soft_ceiling)),
    )
    return past_pool[:SOFT_CAP_TEAM_SIZE]


def _select_p2_blend(inputs: _SelectionInputs) -> list[KippoUser]:
    past_pool = sorted(
        (u for u in inputs.org_pool if u.id in inputs.past_members),
        key=lambda u: (-inputs.all_time_hours.get(u.id, 0), _by_capacity_then_id(u, inputs.capacity, inputs.member_soft_ceiling)),
    )
    team = past_pool[:SOFT_CAP_TEAM_SIZE]
    non_past = sorted(
        (u for u in inputs.org_pool if u.id not in inputs.past_members),
        key=lambda u: _by_capacity_then_id(u, inputs.capacity, inputs.member_soft_ceiling),
    )
    for candidate in non_past:
        if len(team) >= SOFT_CAP_TEAM_SIZE:
            break
        team.append(candidate)
    return team


def _select_p3_most_available(inputs: _SelectionInputs) -> list[KippoUser]:
    return sorted(inputs.org_pool, key=lambda u: _by_capacity_then_id(u, inputs.capacity, inputs.member_soft_ceiling))[:SOFT_CAP_TEAM_SIZE]


def _select_p4_previous_month(inputs: _SelectionInputs) -> list[KippoUser]:
    """P4: every active org member who had a non-zero prior-month allocation on this project.

    Returns empty when no prior-month assignments exist, so the strategy is silently dropped
    by `compute()` (same flow as P1's S2 greenfield skip). Inactive users from the prior
    month are excluded because they are not in `org_pool`.
    """
    if not inputs.previous_month_user_pct:
        return []
    by_id = {u.id: u for u in inputs.org_pool}
    return [by_id[user_id] for user_id in inputs.previous_month_user_pct if user_id in by_id]


_STRATEGIES: tuple[_PatternStrategy, ...] = (
    _PatternStrategy(PatternId.P1_MAX_REUSE, _select_p1_past_only),
    _PatternStrategy(PatternId.P2_BLEND, _select_p2_blend),
    _PatternStrategy(PatternId.P3_MOST_AVAILABLE, _select_p3_most_available),
    _PatternStrategy(PatternId.P4_PREVIOUS_MONTH, _select_p4_previous_month),
)


class ProjectAssignmentSuggestionManager:
    """Generate ranked assignment patterns for a project.

    Construct with a project (and optional `from_month`), call `compute()` to get
    the list of candidate patterns. Returns 0–3 `ProjectAssignmentPattern` objects after pattern
    generation, dedup (S3), and ranking (B10).

    See monkut/kippo#224 B1–B13 and monkut/kippo#227 clarifications S1–S4.
    """

    def __init__(self, project: KippoProject, from_month: datetime.date | None = None) -> None:
        self.project = project
        self.from_month = from_month
        # Reused across all per-pattern forecast invocations so the manager can memoize
        # repeated logged-hours / latest-week-start lookups.
        self._forecaster = ProjectAssignmentForecastManager(project)

    def compute(self) -> list[ProjectAssignmentPattern]:
        if self.project.start_date is None:
            raise ProjectStartDateRequiredError(f"project {self.project.pk} has no start_date")

        from_month = self.from_month or first_of_next_month(timezone.localdate())

        inputs = _SelectionInputs(
            org_pool=self.__org_pool(),
            past_members=self.__past_members(),
            capacity=self.__capacity_lookup(self.project.organization, from_month),
            all_time_hours=self.__all_time_logged_hours_per_user(),
            member_soft_ceiling=self.project.organization.project_assignment_member_soft_ceiling,
            previous_month_user_pct=self.__previous_month_user_pct(from_month),
        )

        patterns: list[ProjectAssignmentPattern] = []
        for strategy in _STRATEGIES:
            team = strategy.select(inputs)
            if not team:
                # S2 (or any future strategy returning an empty team) — skip entirely.
                continue
            patterns.append(self.__build_pattern(strategy.pattern_id, team, inputs, from_month))

        return self.__rank(self.__deduplicate(patterns))

    # ------------------------------------------------------------------ inputs

    def __past_members(self) -> set:
        """B2: past members = users with any prior assignment OR effort row on this project."""
        from_assignments = set(ProjectMonthlyAssignment.objects.filter(project=self.project).values_list("user_id", flat=True))
        from_effort = set(ProjectWeeklyEffort.objects.filter(project=self.project, hours__gt=0).values_list("user_id", flat=True))
        return from_assignments | from_effort

    def __org_pool(self) -> list[KippoUser]:
        """S1: all active org members (KippoUser.is_active=True with active OrganizationMembership)."""
        membership_user_ids = OrganizationMembership.objects.filter(organization=self.project.organization).values_list("user_id", flat=True)
        return list(KippoUser.objects.filter(id__in=membership_user_ids, is_active=True).order_by("id"))

    def __capacity_lookup(self, organization: KippoOrganization, from_month: datetime.date) -> dict:
        """B4: existing percentage commitment per user/month across all projects in the organization.

        Returns `{user_id: {month: existing_total_pct}}`. New pattern percentages will be added on
        top of these values; over-allocation (>100) becomes a `ProjectAssignmentPatternConflict`.
        """
        rows = (
            ProjectMonthlyAssignment.objects.filter(project__organization=organization, month__gte=from_month)
            .values("user_id", "month")
            .annotate(total=Sum("percentage"))
        )
        existing: dict = defaultdict(dict)
        for row in rows:
            existing[row["user_id"]][row["month"]] = row["total"] or 0
        return existing

    def __all_time_logged_hours_per_user(self) -> dict[int, int]:
        """B9 tie-breaker primary key: total all-time `ProjectWeeklyEffort.hours` on this project."""
        rows = ProjectWeeklyEffort.objects.filter(project=self.project, hours__gt=0).values("user_id").annotate(total=Sum("hours"))
        return {row["user_id"]: row["total"] or 0 for row in rows}

    def __previous_month_user_pct(self, from_month: datetime.date) -> dict[int, int]:
        """P4 input: per-user total allocation on this project for the month immediately before from_month.

        Sums across multiple rows for the same (user, prior_month) cell — typically one row per
        user-month, but the Sum guards against double-rows. Users with 0% rows are excluded so
        `_select_p4_previous_month` doesn't propose someone last month already had at zero.
        """
        previous_month = from_month - relativedelta(months=1)
        rows = (
            ProjectMonthlyAssignment.objects.filter(project=self.project, month=previous_month, percentage__gt=0)
            .values("user_id")
            .annotate(total=Sum("percentage"))
        )
        return {row["user_id"]: row["total"] for row in rows if row["total"]}

    # --------------------------------------------------------------- pattern build

    def __build_pattern(
        self,
        pattern_id: PatternId,
        team: list[KippoUser],
        inputs: _SelectionInputs,
        from_month: datetime.date,
    ) -> ProjectAssignmentPattern:
        target_date = self.project.target_date
        if target_date is None:
            end_month = from_month + relativedelta(months=HORIZON_MONTHS_NO_TARGET)
        else:
            end_month = target_date.replace(day=1) + relativedelta(months=HORIZON_MONTHS_PAST_TARGET)

        if pattern_id == PatternId.P4_PREVIOUS_MONTH:
            # Use last month's per-user percentages verbatim; no thin-pattern fallback —
            # the whole point is "this is what last month looked like".
            pct_per_user = {u.id: inputs.previous_month_user_pct[u.id] for u in team}
            overlay, conflicts, estimated, infeasible = self.__evaluate_overlay(
                team, pct_per_user, from_month, end_month, inputs.capacity, target_date
            )
        else:
            baseline_pct = max(ALLOCATION_FLOOR_PERCENTAGE, inputs.member_soft_ceiling // len(team))
            overlay, conflicts, estimated, infeasible = self.__evaluate_overlay(
                team, {u.id: baseline_pct for u in team}, from_month, end_month, inputs.capacity, target_date
            )
            # B11: thin-pattern fallback — push every member to the soft ceiling if the baseline
            # can't meet target_date. We never push past the org soft ceiling on a single project.
            if infeasible:
                overlay, conflicts, estimated, infeasible = self.__evaluate_overlay(
                    team,
                    {u.id: inputs.member_soft_ceiling for u in team},
                    from_month,
                    end_month,
                    inputs.capacity,
                    target_date,
                )

        members = [
            ProjectAssignmentPatternMember(
                user_id=user.id,
                is_past_member=user.id in inputs.past_members,
                monthly_percentages=overlay.get(user.id, {}),
            )
            for user in team
        ]
        return ProjectAssignmentPattern(
            pattern_ids=[pattern_id.value],
            label=PATTERN_LABELS[pattern_id],
            estimated_completion=estimated,
            infeasible=infeasible,
            conflicts=conflicts,
            members=members,
        )

    def __evaluate_overlay(
        self,
        team: list[KippoUser],
        pct_per_user: dict[int, int],
        from_month: datetime.date,
        end_month: datetime.date,
        capacity: dict,
        target_date: datetime.date | None,
    ) -> tuple[dict, list[ProjectAssignmentPatternConflict], datetime.date | None, bool]:
        """Build the overlay using each user's percentage from `pct_per_user`, run the forecast,
        return (overlay, conflicts, estimated, infeasible).
        """
        overlay, conflicts = self.__compose_overlay(team, pct_per_user, from_month, end_month, capacity)
        estimated = self._forecaster.compute(overlay=overlay).estimated_completion_date
        infeasible = estimated is None or (target_date is not None and estimated > target_date)
        return overlay, conflicts, estimated, infeasible

    def __compose_overlay(
        self,
        team: list[KippoUser],
        pct_per_user: dict[int, int],
        from_month: datetime.date,
        end_month: datetime.date,
        capacity: dict,
    ) -> tuple[dict, list[ProjectAssignmentPatternConflict]]:
        """Build the user-month overlay and collect over-allocation conflicts.

        Each (user, month) cell uses `pct_per_user[user.id]`. A
        `ProjectAssignmentPatternConflict` is recorded for any cell where adding the proposed
        pattern would push the user's org-total beyond `MAX_INDIVIDUAL_PERCENTAGE` — per
        kippo#224 D3 the suggester surfaces the conflict rather than silently capping.
        """
        overlay: dict = {}
        conflicts: list[ProjectAssignmentPatternConflict] = []

        current_month = from_month
        while current_month <= end_month:
            for user in team:
                pct = pct_per_user[user.id]
                overlay.setdefault(user.id, {})[current_month] = pct
                existing = capacity.get(user.id, {}).get(current_month, 0)
                if existing + pct > MAX_INDIVIDUAL_PERCENTAGE:
                    conflicts.append(
                        ProjectAssignmentPatternConflict(
                            user_id=user.id,
                            month=current_month,
                            reason=f"already {existing}% on other projects (proposed +{pct}% would total {existing + pct}%)",
                        )
                    )
            current_month = current_month + relativedelta(months=1)

        return overlay, conflicts

    # ----------------------------------------------------------------- post-pass

    @staticmethod
    def __deduplicate(patterns: list[ProjectAssignmentPattern]) -> list[ProjectAssignmentPattern]:
        """S3: collapse Patterns with identical members + monthly_percentages into one.

        Merged pattern's `pattern_ids` lists every strategy that converged. The earliest
        (P1, P2, P3) determines the survivor's primary label.
        """
        canonical: list[ProjectAssignmentPattern] = []
        for pattern in patterns:
            duplicate_of = next((p for p in canonical if _patterns_equivalent(p, pattern)), None)
            if duplicate_of is None:
                canonical.append(pattern)
                continue
            duplicate_of.pattern_ids.extend(pattern.pattern_ids)
            duplicate_of.label = " / ".join(PATTERN_LABELS[PatternId(pid)] for pid in duplicate_of.pattern_ids)
        return canonical

    @staticmethod
    def __rank(patterns: list[ProjectAssignmentPattern]) -> list[ProjectAssignmentPattern]:
        """B10: feasible first; among feasible with target_date, prefer closest to target_date."""
        return sorted(
            patterns,
            key=lambda p: (
                int(p.infeasible),
                p.estimated_completion or datetime.date.max,
            ),
        )


def _patterns_equivalent(a: ProjectAssignmentPattern, b: ProjectAssignmentPattern) -> bool:
    """Two patterns are equivalent when their member set + monthly_percentages match."""
    if len(a.members) != len(b.members):
        return False
    return {m.user_id: m.monthly_percentages for m in a.members} == {m.user_id: m.monthly_percentages for m in b.members}
