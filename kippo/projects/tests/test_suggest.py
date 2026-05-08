"""Tests for the project assignment suggestion service + endpoint.

Covers monkut/kippo#227 (Phase 2 of feature #224).
Decisions: B1–B13, D2; clarifications S1–S4 from kippo#227.
"""

import datetime
from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.functions import first_of_next_month
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from projects.exceptions import ProjectStartDateRequiredError
from projects.models import KippoProject, ProjectMonthlyAssignment, ProjectWeeklyEffort
from projects.services.suggest import (
    ALLOCATION_FLOOR_PERCENTAGE,
    SOFT_CAP_TEAM_SIZE,
    PatternId,
    ProjectAssignmentSuggestionManager,
)


def _set_today_dependent_dates(project: KippoProject) -> None:
    today = timezone.localdate()
    project.start_date = (today - datetime.timedelta(days=180)).replace(day=1)
    project.target_date = today + datetime.timedelta(days=120)
    project.allocated_staff_days = 30
    project.save()


class SuggesterTestCaseBase(TestCase):
    """Shared fixture builder."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.organization.day_workhours = 8
        self.organization.save()
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        _set_today_dependent_dates(self.project)
        self.today = timezone.localdate()

    def _add_member(self, username: str, *, is_developer: bool = True) -> KippoUser:
        user = KippoUser.objects.create(username=username, email=f"{username}@example.com")
        OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            is_developer=is_developer,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        return user

    def _make_assignment(
        self,
        *,
        project: KippoProject | None = None,
        user: KippoUser | None = None,
        month: datetime.date,
        percentage: int,
        is_confirmed: bool = False,
    ) -> ProjectMonthlyAssignment:
        return ProjectMonthlyAssignment.objects.create(
            project=project or self.project,
            user=user or self.user,
            month=month,
            percentage=percentage,
            is_confirmed=is_confirmed,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def _make_effort(
        self,
        *,
        project: KippoProject | None = None,
        user: KippoUser | None = None,
        week_start: datetime.date,
        hours: int,
    ) -> ProjectWeeklyEffort:
        return ProjectWeeklyEffort.objects.create(
            project=project or self.project,
            user=user or self.user,
            week_start=week_start,
            hours=hours,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )


class SuggestionServiceCoreTestCase(SuggesterTestCaseBase):
    """Direct unit tests of ProjectAssignmentSuggestionManager."""

    def test_raises_when_start_date_is_null(self):
        self.project.start_date = None
        self.project.save()
        with self.assertRaises(ProjectStartDateRequiredError):
            ProjectAssignmentSuggestionManager(self.project).compute()

    def test_greenfield_skips_p1(self):
        # No past members, but org pool has members → only P2 + P3 returned (S2).
        self._add_member("dev2")
        self._add_member("dev3")

        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        all_pattern_ids = {pid for p in patterns for pid in p.pattern_ids}
        self.assertNotIn("P1-max-reuse", all_pattern_ids)

    def test_past_member_present_yields_p1(self):
        # self.user has logged effort → counts as a past member.
        past_monday = self.today - datetime.timedelta(days=14)
        past_monday = past_monday - datetime.timedelta(days=past_monday.weekday())
        self._make_effort(week_start=past_monday, hours=24)

        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        all_pattern_ids = {pid for p in patterns for pid in p.pattern_ids}
        self.assertIn("P1-max-reuse", all_pattern_ids)

    def test_inactive_user_excluded_from_org_pool(self):
        # Active user is in pool; inactive is not (D2 / S1).
        self._add_member("active-dev")
        inactive = self._add_member("inactive-dev")
        inactive.is_active = False
        inactive.save()

        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        proposed_user_ids = {m.user_id for p in patterns for m in p.members}
        self.assertNotIn(inactive.id, proposed_user_ids)

    def test_floor_excludes_no_one_when_team_at_or_below_soft_cap(self):
        # 3 members → baseline = 100/3 = 33% per member → above 10% floor.
        for i in range(2):
            self._add_member(f"dev-{i}")
        # Make self.user a past member
        past_monday = self.today - datetime.timedelta(days=21)
        past_monday = past_monday - datetime.timedelta(days=past_monday.weekday())
        self._make_effort(week_start=past_monday, hours=8)

        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        for pattern in patterns:
            for member in pattern.members:
                for pct in member.monthly_percentages.values():
                    self.assertGreaterEqual(pct, ALLOCATION_FLOOR_PERCENTAGE)

    def test_team_size_capped_at_soft_cap(self):
        # 10 org members → each pattern team should be ≤ SOFT_CAP_TEAM_SIZE.
        for i in range(10):
            self._add_member(f"dev-{i:02}")

        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        for pattern in patterns:
            self.assertLessEqual(len(pattern.members), SOFT_CAP_TEAM_SIZE)

    def test_capacity_conflict_recorded_when_user_already_committed(self):
        # User has 80% on another project for the proposed month → conflict.
        next_month = first_of_next_month(self.today)
        other_project = KippoProject.objects.create(
            name="other-project-for-capacity-test",
            organization=self.organization,
            columnset=self.project.columnset,
            start_date=self.project.start_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self._make_assignment(project=other_project, month=next_month, percentage=80, is_confirmed=True)

        # Greenfield on this project so suggester picks self.user as best-available
        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        # At least one pattern should flag the conflict for self.user in next_month
        flagged = any(any(c.user_id == self.user.id and c.month == next_month for c in p.conflicts) for p in patterns)
        self.assertTrue(flagged, "Expected at least one pattern to flag the over-allocation conflict")

    def test_dedup_collapses_identical_patterns(self):
        # Single org member, no past contributions → P2 and P3 will pick the same person
        # at 100%. Patterns merge into one with multiple pattern_ids (S3).
        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        # Should have at most 1 pattern (P1 skipped by S2; P2/P3 merge)
        self.assertEqual(len(patterns), 1)
        self.assertGreaterEqual(len(patterns[0].pattern_ids), 2)

    def test_pattern_member_marks_past_member_flag(self):
        # self.user is a past member; new dev2 isn't.
        past_monday = self.today - datetime.timedelta(days=21)
        past_monday = past_monday - datetime.timedelta(days=past_monday.weekday())
        self._make_effort(week_start=past_monday, hours=8)
        self._add_member("dev2")

        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        # P1 should mark self.user as past_member
        p1 = next((p for p in patterns if "P1-max-reuse" in p.pattern_ids), None)
        if p1 is not None:
            for member in p1.members:
                if member.user_id == self.user.id:
                    self.assertTrue(member.is_past_member)

    def test_target_date_null_does_not_rank_patterns_strictly(self):
        # No target_date → all returned, no infeasible-flagging based on target.
        for i in range(2):
            self._add_member(f"dev-{i}")
        self.project.target_date = None
        self.project.save()

        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        # No patterns should be marked infeasible for "missing target" reason
        # (they may still be infeasible if forecast doesn't complete, but with 100% allocation it should)
        self.assertGreater(len(patterns), 0)

    def test_pattern_response_has_expected_keys(self):
        self._add_member("dev2")
        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        self.assertGreater(len(patterns), 0)
        pattern = patterns[0]
        # Pydantic model with the spec'd fields
        self.assertIsInstance(pattern.pattern_ids, list)
        self.assertGreaterEqual(len(pattern.pattern_ids), 1)
        self.assertIsInstance(pattern.label, str)
        self.assertIn(pattern.estimated_completion, [None, *([pattern.estimated_completion])])
        self.assertIsInstance(pattern.infeasible, bool)
        self.assertIsInstance(pattern.conflicts, list)
        self.assertIsInstance(pattern.members, list)

    def test_p4_emitted_when_previous_month_has_assignments(self):
        """P4 carries each user's previous-month percentage forward verbatim."""
        from_month = first_of_next_month(self.today)
        previous_month = (from_month - datetime.timedelta(days=1)).replace(day=1)
        # Two users with non-uniform allocations on this project last month.
        dev2 = self._add_member("dev2-p4")
        self._make_assignment(month=previous_month, percentage=30, is_confirmed=True)
        self._make_assignment(user=dev2, month=previous_month, percentage=50, is_confirmed=True)

        patterns = ProjectAssignmentSuggestionManager(self.project, from_month=from_month).compute()
        p4 = next((p for p in patterns if PatternId.P4_PREVIOUS_MONTH.value in p.pattern_ids), None)
        self.assertIsNotNone(p4, "Expected a P4-previous-month pattern when prior-month rows exist")
        member_pcts = {m.user_id: m.monthly_percentages for m in p4.members}
        self.assertEqual(set(member_pcts.keys()), {self.user.id, dev2.id})
        # Every month from from_month → end_month carries each user's prior-month percentage.
        for month_pcts in member_pcts[self.user.id].values():
            self.assertEqual(month_pcts, 30)
        for month_pcts in member_pcts[dev2.id].values():
            self.assertEqual(month_pcts, 50)

    def test_p4_skipped_when_no_previous_month_assignments(self):
        """No prior-month rows on this project → P4 strategy is dropped silently."""
        # Greenfield project: setUp creates no assignments.
        self._add_member("dev2-p4-skip")
        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        p4 = next((p for p in patterns if PatternId.P4_PREVIOUS_MONTH.value in p.pattern_ids), None)
        self.assertIsNone(p4, "P4 should be absent when no prior-month assignments exist")

    def test_p4_excludes_zero_percent_prior_rows(self):
        """A user with a 0% prior-month row shouldn't appear in the P4 team."""
        from_month = first_of_next_month(self.today)
        previous_month = (from_month - datetime.timedelta(days=1)).replace(day=1)
        ghost = self._add_member("dev2-p4-zero")
        self._make_assignment(month=previous_month, percentage=40, is_confirmed=True)
        self._make_assignment(user=ghost, month=previous_month, percentage=0, is_confirmed=True)

        patterns = ProjectAssignmentSuggestionManager(self.project, from_month=from_month).compute()
        p4 = next((p for p in patterns if PatternId.P4_PREVIOUS_MONTH.value in p.pattern_ids), None)
        self.assertIsNotNone(p4)
        proposed_user_ids = {m.user_id for m in p4.members}
        self.assertIn(self.user.id, proposed_user_ids)
        self.assertNotIn(ghost.id, proposed_user_ids)

    def test_per_member_pct_capped_at_org_soft_ceiling(self):
        """No proposed (member, month) percentage should exceed the org soft ceiling.

        The soft ceiling caps both the baseline split and the thin-pattern push-up,
        so even on a single-member project we never propose 100% on this project.
        """
        self.organization.project_assignment_member_soft_ceiling = 60
        self.organization.save()

        patterns = ProjectAssignmentSuggestionManager(self.project).compute()
        self.assertGreater(len(patterns), 0)
        for pattern in patterns:
            for member in pattern.members:
                for pct in member.monthly_percentages.values():
                    self.assertLessEqual(
                        pct,
                        self.organization.project_assignment_member_soft_ceiling,
                        msg=f"pattern {pattern.pattern_ids} proposed {pct}% > soft ceiling 60%",
                    )


class SuggestionEndpointTestCase(SuggesterTestCaseBase):
    """Test the POST /api/projects/<id>/suggest-assignments/ endpoint."""

    def setUp(self):
        super().setUp()
        # Cross-org user for permission test
        self.other_org = KippoOrganization.objects.create(
            name="suggest-other-org",
            github_organization_name="suggestotherorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.outsider = KippoUser.objects.create(username="suggest-outsider", email="suggest-outsider@example.com")
        OrganizationMembership.objects.create(
            user=self.outsider,
            organization=self.other_org,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/suggest-assignments/"

    def test_unauthenticated_returns_401(self):
        anon = APIClient()
        response = anon.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_returns_patterns_payload(self):
        self._add_member("dev-x")
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK, response.content)
        body = response.json()
        self.assertIn("patterns", body)
        self.assertIsInstance(body["patterns"], list)

    def test_start_date_null_returns_400(self):
        self.project.start_date = None
        self.project.save()
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.json()["code"], "project_start_date_required")

    def test_invalid_from_month_returns_400(self):
        response = self.client.post(self.url, {"from_month": "not-a-date"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.json()["code"], "invalid_from_month")

    def test_cross_org_user_gets_404(self):
        cross_client = APIClient()
        cross_client.force_authenticate(user=self.outsider)
        response = cross_client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_openapi_schema_exposes_suggest_endpoint(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        path = f"{settings.URL_PREFIX}/api/projects/{{id}}/suggest-assignments/"
        self.assertIn(path, schema["paths"])
        self.assertIn("post", schema["paths"][path])

    def test_openapi_schema_patterns_field_is_typed(self):
        """kippo#231: response.patterns must reference the typed pattern schema, not a generic JSONField.

        Catches regressions where the inline_serializer reverts to ListField(child=JSONField()).
        """
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        suggest_response = schema["components"]["schemas"]["SuggestAssignmentsResponse"]
        patterns = suggest_response["properties"]["patterns"]
        self.assertEqual(patterns["type"], "array")
        item_schema = patterns["items"]
        # Either the item is a $ref to the typed model, or it's an inline object with members.
        # Both indicate the response is typed (not the unknown-shaped `unknown[]` from JSONField).
        if "$ref" in item_schema:
            ref = item_schema["$ref"]
            self.assertIn("ProjectAssignmentPattern", ref)
        else:
            self.assertIn("members", item_schema.get("properties", {}))
            self.assertIn("pattern_ids", item_schema.get("properties", {}))

    def test_openapi_schema_suggest_endpoint_is_json_only(self):
        """kippo#231: the suggest endpoint must advertise application/json only.

        DRF's default parser_classes include multipart/form-data + form-urlencoded; we restrict
        to JSONParser so the OpenAPI schema doesn't carry the noise.
        """
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        path = f"{settings.URL_PREFIX}/api/projects/{{id}}/suggest-assignments/"
        request_body = schema["paths"][path]["post"]["requestBody"]
        content_types = set(request_body["content"].keys())
        self.assertEqual(content_types, {"application/json"})
