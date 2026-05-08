"""Project assignment pattern suggester.

Generates 0–3 candidate `ProjectMonthlyAssignment` patterns for a project, ranked
by closeness to `project.target_date`. Patterns vary along a continuity gradient
(P1 max past-member reuse, P2 blend past + org pool, P3 most-available pool). See
monkut/kippo#224 decisions B1–B13 + monkut/kippo#227 clarifications S1–S4.
"""

from __future__ import annotations

import datetime
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.functions import first_of_next_month
from django.db.models import Sum
from django.utils import timezone

from projects.definitions import Pattern, PatternConflict, PatternMember
from projects.exceptions import ProjectStartDateRequiredError
from projects.models import ProjectMonthlyAssignment, ProjectWeeklyEffort
from projects.services.forecast import ProjectAssignmentForecastManager

if TYPE_CHECKING:
    from projects.models import KippoProject

logger = logging.getLogger(__name__)

ALLOCATION_FLOOR_PERCENTAGE = 10  # B5
SOFT_CAP_TEAM_SIZE = 3  # B6
MAX_INDIVIDUAL_PERCENTAGE = 100
THIN_PATTERN_PUSH_PERCENTAGE = 100  # B11 — push to 100% per user-month when capacity insufficient
HORIZON_MONTHS_NO_TARGET = 24  # walk this many months when project.target_date is null
HORIZON_MONTHS_PAST_TARGET = 6  # walk this many months past target for the infeasible-flag path

PATTERN_LABELS = {
    "P1-max-reuse": "Maximum past-member reuse",
    "P2-blend": "Blend of past members and org pool",
    "P3-most-available": "Most-available org pool members",
}


def _add_months(reference: datetime.date, months: int) -> datetime.date:
    total_months = reference.year * 12 + (reference.month - 1) + months
    year, month = divmod(total_months, 12)
    return datetime.date(year, month + 1, 1)


class ProjectAssignmentSuggestionManager:
    """Generate ranked assignment patterns for a project.

    Construct with a project (and optional `from_month`), call `compute()` to get
    the list of candidate patterns. Returns 0–3 `Pattern` objects after pattern
    generation, dedup (S3), and ranking (B10).

    See monkut/kippo#224 B1–B13 and monkut/kippo#227 clarifications S1–S4.
    """

    def __init__(self, project: KippoProject, from_month: datetime.date | None = None) -> None:
        self.project = project
        self.from_month = from_month

    def compute(self) -> list[Pattern]:
        project = self.project
        if project.start_date is None:
            raise ProjectStartDateRequiredError(f"project {project.pk} has no start_date")

        from_month = self.from_month or first_of_next_month(timezone.localdate())
        organization = project.organization

        past_members = self.__past_members()
        org_pool = self.__org_pool()
        capacity = self.__capacity_lookup(organization, from_month)
        all_time_hours = self.__all_time_logged_hours_per_user()

        patterns: list[Pattern] = []
        for pattern_id, team in self.__candidate_teams(past_members, org_pool, capacity, all_time_hours):
            if pattern_id == "P1-max-reuse" and not past_members:
                # S2: greenfield project — skip P1 entirely
                continue
            pattern = self.__build_pattern(
                pattern_id=pattern_id,
                team=team,
                past_members=past_members,
                from_month=from_month,
                capacity=capacity,
            )
            patterns.append(pattern)

        patterns = self.__deduplicate(patterns)
        return self.__rank(patterns)

    # ------------------------------------------------------------------ inputs

    def __past_members(self) -> set[int]:
        """B2: past members = users with any prior assignment OR effort row on this project."""
        from_assignments = set(ProjectMonthlyAssignment.objects.filter(project=self.project).values_list("user_id", flat=True))
        from_effort = set(ProjectWeeklyEffort.objects.filter(project=self.project, hours__gt=0).values_list("user_id", flat=True))
        return from_assignments | from_effort

    def __org_pool(self) -> list[KippoUser]:
        """S1: all active org members (KippoUser.is_active=True with active OrganizationMembership)."""
        membership_user_ids = OrganizationMembership.objects.filter(organization=self.project.organization).values_list("user_id", flat=True)
        return list(KippoUser.objects.filter(id__in=membership_user_ids, is_active=True).order_by("id"))

    def __capacity_lookup(self, organization: KippoOrganization, from_month: datetime.date) -> dict[int, dict[datetime.date, int]]:
        """B4: existing percentage commitment per user/month across all projects in the organization.

        Returns `{user_id: {month: existing_total_pct}}`. New pattern percentages will be added on
        top of these values; over-allocation (>100) becomes a `PatternConflict`.
        """
        rows = (
            ProjectMonthlyAssignment.objects.filter(project__organization=organization, month__gte=from_month)
            .values("user_id", "month")
            .annotate(total=Sum("percentage"))
        )
        existing: dict[int, dict[datetime.date, int]] = defaultdict(dict)
        for row in rows:
            existing[row["user_id"]][row["month"]] = row["total"] or 0
        return existing

    def __all_time_logged_hours_per_user(self) -> dict[int, int]:
        """B9 tie-breaker primary key: total all-time `ProjectWeeklyEffort.hours` on this project."""
        rows = ProjectWeeklyEffort.objects.filter(project=self.project, hours__gt=0).values("user_id").annotate(total=Sum("hours"))
        return {row["user_id"]: row["total"] or 0 for row in rows}

    def __candidate_teams(
        self,
        past_members: set[int],
        org_pool: list[KippoUser],
        capacity: dict[int, dict[datetime.date, int]],
        all_time_hours: dict[int, int],
    ) -> list[tuple[str, list[KippoUser]]]:
        """Build the (pattern_id, team) pairs for P1 / P2 / P3.

        Team selection per B3 / B7 / B8 + tie-breaker B9 (descending all-time hours,
        then descending current available capacity). Soft cap of 3 (B6) applied to all.
        """

        def current_available(user_id: int) -> int:
            user_existing = capacity.get(user_id, {})
            if not user_existing:
                return MAX_INDIVIDUAL_PERCENTAGE
            return MAX_INDIVIDUAL_PERCENTAGE - max(user_existing.values())

        def sort_key(user: KippoUser) -> tuple[int, int]:
            return (-all_time_hours.get(user.id, 0), -current_available(user.id))

        past_pool = [u for u in org_pool if u.id in past_members]
        past_pool.sort(key=sort_key)

        # P1: past members only (B3)
        p1 = past_pool[:SOFT_CAP_TEAM_SIZE]

        # P2: past members + org top-up by capacity (B7)
        non_past = [u for u in org_pool if u.id not in past_members]
        non_past.sort(key=lambda u: (-current_available(u.id), u.id))
        p2 = past_pool[:SOFT_CAP_TEAM_SIZE]
        for candidate in non_past:
            if len(p2) >= SOFT_CAP_TEAM_SIZE:
                break
            p2.append(candidate)

        # P3: full org pool ranked by capacity, no past-member preference (B8)
        org_sorted = sorted(org_pool, key=lambda u: (-current_available(u.id), u.id))
        p3 = org_sorted[:SOFT_CAP_TEAM_SIZE]

        return [("P1-max-reuse", p1), ("P2-blend", p2), ("P3-most-available", p3)]

    # --------------------------------------------------------------- pattern build

    def __build_pattern(
        self,
        *,
        pattern_id: str,
        team: list[KippoUser],
        past_members: set[int],
        from_month: datetime.date,
        capacity: dict[int, dict[datetime.date, int]],
    ) -> Pattern:
        if not team:
            return Pattern(
                pattern_ids=[pattern_id],
                label=PATTERN_LABELS[pattern_id],
                estimated_completion=None,
                infeasible=True,
                conflicts=[],
                members=[],
            )

        team_size = len(team)
        baseline_pct = max(ALLOCATION_FLOOR_PERCENTAGE, MAX_INDIVIDUAL_PERCENTAGE // team_size)
        target_date = self.project.target_date
        end_month = self.__horizon_end(from_month, target_date)

        # Pass 1: baseline distribution (per-member equal share).
        overlay, conflicts = self.__compose_overlay(team, baseline_pct, from_month, end_month, capacity)
        forecast = ProjectAssignmentForecastManager(self.project).compute(overlay=overlay)
        estimated = forecast.estimated_completion_date

        # B11: if the baseline distribution can't meet target_date (or didn't complete at all),
        # push every member to 100% per user-month and re-evaluate. Mark infeasible if still not.
        infeasible = self.__is_infeasible(estimated, target_date)
        if infeasible:
            overlay, conflicts = self.__compose_overlay(team, THIN_PATTERN_PUSH_PERCENTAGE, from_month, end_month, capacity)
            forecast = ProjectAssignmentForecastManager(self.project).compute(overlay=overlay)
            estimated = forecast.estimated_completion_date
            infeasible = self.__is_infeasible(estimated, target_date)

        members = [
            PatternMember(
                user_id=user.id,
                is_past_member=user.id in past_members,
                monthly_percentages=overlay.get(user.id, {}),
            )
            for user in team
        ]
        return Pattern(
            pattern_ids=[pattern_id],
            label=PATTERN_LABELS[pattern_id],
            estimated_completion=estimated,
            infeasible=infeasible,
            conflicts=conflicts,
            members=members,
        )

    def __horizon_end(self, from_month: datetime.date, target_date: datetime.date | None) -> datetime.date:
        if target_date is None:
            return _add_months(from_month, HORIZON_MONTHS_NO_TARGET)
        target_first_of_month = target_date.replace(day=1)
        return _add_months(target_first_of_month, HORIZON_MONTHS_PAST_TARGET)

    def __compose_overlay(
        self,
        team: list[KippoUser],
        per_member_pct: int,
        from_month: datetime.date,
        end_month: datetime.date,
        capacity: dict[int, dict[datetime.date, int]],
    ) -> tuple[dict[int, dict[datetime.date, int]], list[PatternConflict]]:
        """Build the user-month overlay and collect over-allocation conflicts.

        Pattern percentage is fixed at `per_member_pct`. A `PatternConflict` is recorded for any
        (user, month) where adding the proposed pattern would push the user's org-total beyond
        `MAX_INDIVIDUAL_PERCENTAGE`. The pattern still proposes the requested percentage —
        per kippo#224 D3 the suggester surfaces the conflict rather than silently capping.
        """
        overlay: dict[int, dict[datetime.date, int]] = {}
        conflicts: list[PatternConflict] = []

        current_month = from_month
        while current_month <= end_month:
            for user in team:
                overlay.setdefault(user.id, {})[current_month] = per_member_pct
                existing = capacity.get(user.id, {}).get(current_month, 0)
                if existing + per_member_pct > MAX_INDIVIDUAL_PERCENTAGE:
                    conflicts.append(
                        PatternConflict(
                            user_id=user.id,
                            month=current_month,
                            reason=f"already {existing}% on other projects (proposed +{per_member_pct}% would total {existing + per_member_pct}%)",
                        )
                    )
            current_month = _add_months(current_month, 1)

        return overlay, conflicts

    @staticmethod
    def __is_infeasible(estimated: datetime.date | None, target_date: datetime.date | None) -> bool:
        if estimated is None:
            return True
        if target_date is None:
            return False
        return estimated > target_date

    # ----------------------------------------------------------------- post-pass

    @staticmethod
    def __deduplicate(patterns: list[Pattern]) -> list[Pattern]:
        """S3: collapse Patterns with identical members + monthly_percentages into one.

        Merged pattern's `pattern_ids` lists every strategy that converged. The earliest
        (P1, P2, P3) determines the survivor's primary label.
        """
        if not patterns:
            return []

        canonical: list[Pattern] = []
        for pattern in patterns:
            duplicate_of = None
            for existing in canonical:
                if _patterns_equivalent(existing, pattern):
                    duplicate_of = existing
                    break
            if duplicate_of:
                duplicate_of.pattern_ids.extend(pattern.pattern_ids)
                duplicate_of.label = " / ".join(PATTERN_LABELS[pid] for pid in duplicate_of.pattern_ids)
            else:
                canonical.append(pattern)
        return canonical

    @staticmethod
    def __rank(patterns: list[Pattern]) -> list[Pattern]:
        """B10: feasible first; among feasible with target_date, prefer closest to target_date."""

        def sort_key(pattern: Pattern) -> tuple[int, int]:
            # 0 = feasible, 1 = infeasible (for stable sort; feasible first)
            feasibility = 1 if pattern.infeasible else 0
            distance = 10**9
            if pattern.estimated_completion is not None and not pattern.infeasible:
                distance = pattern.estimated_completion.toordinal()
            return (feasibility, -distance)

        return sorted(patterns, key=sort_key)


def _patterns_equivalent(a: Pattern, b: Pattern) -> bool:
    """Two patterns are equivalent when their member set + monthly_percentages match."""
    if len(a.members) != len(b.members):
        return False
    a_map = {m.user_id: m.monthly_percentages for m in a.members}
    b_map = {m.user_id: m.monthly_percentages for m in b.members}
    return a_map == b_map
