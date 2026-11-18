"""ProjectParticipant model, services and KippoProject admin inline — kiconiaworks/kippo#75.

"Today" is pinned to TODAY (current month = 2026-04); the project period is 2026-03 … 2026-06.
"""

import datetime
import importlib
from unittest.mock import patch

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.apps import apps as django_apps
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.test import RequestFactory, TestCase

from projects.admin import KippoProjectAdmin, ProjectParticipantInline
from projects.definitions import ProjectRoles
from projects.models import KippoProject, ProjectMonthlyAssignment, ProjectParticipant
from projects.services.autoassign import auto_create_future_assignments
from projects.services.participants import apply_defaults_to_remaining_months, create_remaining_assignments, remaining_months
from projects.tests.helpers import make_participant

TODAY = datetime.date(2026, 4, 10)
EDITED_PERCENTAGE = 25


class ParticipantTestBase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.project.start_date = datetime.date(2026, 3, 15)
        self.project.target_date = datetime.date(2026, 6, 10)
        self.project.save()
        self.github_manager = KippoUser.objects.get(username="github-manager")

        self.member = KippoUser.objects.create(username="participant", email="participant@example.com")
        OrganizationMembership.objects.create(
            user=self.member, organization=self.organization, created_by=self.github_manager, updated_by=self.github_manager
        )
        other_org = KippoOrganization.objects.create(
            name="other-org", github_organization_name="otherorg", created_by=self.github_manager, updated_by=self.github_manager
        )
        self.outsider = KippoUser.objects.create(username="outsider", email="outsider@example.com")
        OrganizationMembership.objects.create(
            user=self.outsider, organization=other_org, created_by=self.github_manager, updated_by=self.github_manager
        )

        today_patcher = patch("django.utils.timezone.localdate", return_value=TODAY)
        today_patcher.start()
        self.addCleanup(today_patcher.stop)

    def _participant(self, user: KippoUser | None = None, percentage: int = 40, role: str = "") -> ProjectParticipant:
        participant = ProjectParticipant(project=self.project, user=user or self.member, percentage=percentage, role=role)
        participant.save()
        return participant

    def _months(self, participant: ProjectParticipant) -> list[datetime.date]:
        return list(participant.monthly_assignments.order_by("month").values_list("month", flat=True))


class ProjectParticipantModelTestCase(ParticipantTestBase):
    def test_user_outside_project_organization_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self._participant(user=self.outsider)
        self.assertIn("user", ctx.exception.error_dict)

    def test_missing_project_period_rejected_on_add(self):
        self.project.target_date = None
        self.project.save()
        with self.assertRaises(ValidationError) as ctx:
            self._participant()
        self.assertEqual(ctx.exception.error_dict["__all__"][0].code, "project_period_required")

    def test_user_cannot_be_changed(self):
        participant = self._participant()
        participant.user = self.user
        with self.assertRaises(ValidationError) as ctx:
            participant.save()
        self.assertIn("user", ctx.exception.error_dict)

    def test_delete_removes_current_and_future_rows_keeps_past(self):
        participant = self._participant()
        past = ProjectMonthlyAssignment.objects.create(
            project=self.project, user=self.member, month=datetime.date(2026, 3, 1), percentage=40, participant=participant
        )
        create_remaining_assignments(participant, self.github_manager)

        participant.delete()

        rows = ProjectMonthlyAssignment.objects.filter(project=self.project, user=self.member)
        self.assertEqual(list(rows.values_list("month", flat=True)), [datetime.date(2026, 3, 1)])
        past.refresh_from_db()
        self.assertIsNone(past.participant_id)
        # The kept past row stays editable without a participant.
        past.percentage = 10
        past.save()


class MonthlyAssignmentRequiresParticipantTestCase(ParticipantTestBase):
    def test_new_row_without_participant_rejected(self):
        row = ProjectMonthlyAssignment(project=self.project, user=self.member, month=datetime.date(2026, 4, 1), percentage=10)
        with self.assertRaises(ValidationError) as ctx:
            row.save()
        self.assertEqual(ctx.exception.error_dict["__all__"][0].code, "participant_required")

    def test_new_row_links_participant(self):
        participant = make_participant(self.project, self.member)
        row = ProjectMonthlyAssignment.objects.create(project=self.project, user=self.member, month=datetime.date(2026, 4, 1), percentage=10)
        self.assertEqual(row.participant_id, participant.id)

    def test_auto_extend_links_participant_and_skips_non_participants(self):
        participant = make_participant(self.project, self.member)
        ProjectMonthlyAssignment.objects.create(project=self.project, user=self.member, month=datetime.date(2026, 3, 1), percentage=50)
        created, _skip = auto_create_future_assignments(self.project, self.github_manager)
        self.assertTrue(created)
        self.assertTrue(all(row.participant_id == participant.id for row in created))


class ParticipantServicesTestCase(ParticipantTestBase):
    def test_remaining_months_starts_at_current_month(self):
        self.assertEqual(remaining_months(self.project), [datetime.date(2026, m, 1) for m in (4, 5, 6)])

    def test_remaining_months_empty_when_period_ended_or_missing(self):
        self.project.target_date = datetime.date(2026, 3, 31)
        self.assertEqual(remaining_months(self.project), [])
        self.project.target_date = None
        self.assertEqual(remaining_months(self.project), [])

    def test_create_links_unlinked_rows_and_skips_their_months(self):
        participant = make_participant(self.project, self.member, percentage=40)
        orphan = ProjectMonthlyAssignment.objects.create(
            project=self.project, user=self.member, month=datetime.date(2026, 5, 1), percentage=70, participant=participant
        )
        ProjectMonthlyAssignment.objects.filter(pk=orphan.pk).update(participant=None)

        created, skipped = create_remaining_assignments(participant, self.github_manager)

        self.assertEqual([r.month for r in created], [datetime.date(2026, 4, 1), datetime.date(2026, 6, 1)])
        self.assertEqual(skipped, [datetime.date(2026, 5, 1)])
        orphan.refresh_from_db()
        self.assertEqual((orphan.participant_id, orphan.percentage), (participant.id, 70))

    def test_apply_defaults_updates_current_and_future_rows_only(self):
        participant = self._participant(percentage=40)
        ProjectMonthlyAssignment.objects.create(project=self.project, user=self.member, month=datetime.date(2026, 3, 1), percentage=40)
        create_remaining_assignments(participant, self.github_manager)
        participant.percentage = EDITED_PERCENTAGE
        participant.role = ProjectRoles.TESTER.value
        participant.save()

        updated = apply_defaults_to_remaining_months(participant, self.github_manager)

        self.assertEqual(updated, 3)
        values = dict(participant.monthly_assignments.values_list("month", "percentage"))
        self.assertEqual(values[datetime.date(2026, 3, 1)], 40)
        self.assertTrue(all(values[datetime.date(2026, m, 1)] == EDITED_PERCENTAGE for m in (4, 5, 6)))


class ProjectParticipantInlineTestCase(ParticipantTestBase):
    """The KippoProject admin participants inline: org-scoped user picker, and save_formset runs R1 / E2 / X1."""

    def setUp(self):
        super().setUp()
        self.github_manager.is_superuser = True
        self.github_manager.is_staff = True
        self.github_manager.save()
        self.request = RequestFactory().post("/")
        self.request.user = self.github_manager
        self.site = AdminSite()
        self.modeladmin = KippoProjectAdmin(KippoProject, self.site)
        self.inline = ProjectParticipantInline(KippoProject, self.site)

    def _formset(self, rows: list[dict], initial_count: int = 0) -> BaseInlineFormSet:
        formset_class = self.inline.get_formset(self.request, self.project)
        prefix = formset_class.get_default_prefix()
        data = {
            f"{prefix}-TOTAL_FORMS": str(len(rows)),
            f"{prefix}-INITIAL_FORMS": str(initial_count),
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, row in enumerate(rows):
            for key, value in row.items():
                data[f"{prefix}-{index}-{key}"] = value
        formset = formset_class(data=data, instance=self.project, prefix=prefix)
        self.assertTrue(formset.is_valid(), formset.errors)
        return formset

    def test_user_choices_limited_to_project_organization(self):
        formset_class = self.inline.get_formset(self.request, self.project)
        user_ids = set(formset_class.form.base_fields["user"].queryset.values_list("id", flat=True))
        self.assertIn(self.member.id, user_ids)
        self.assertNotIn(self.outsider.id, user_ids)

    def test_add_creates_remaining_months(self):
        formset = self._formset([{"user": str(self.member.id), "percentage": "30", "role": ""}])
        self.modeladmin.save_formset(self.request, None, formset, change=True)
        participant = ProjectParticipant.objects.get(project=self.project, user=self.member)
        self.assertEqual(self._months(participant), [datetime.date(2026, m, 1) for m in (4, 5, 6)])
        self.assertEqual(participant.created_by, self.github_manager)

    def test_edit_defaults_updates_remaining_months(self):
        participant = self._participant(percentage=40)
        create_remaining_assignments(participant, self.github_manager)
        formset = self._formset(
            [{"id": str(participant.id), "project": str(self.project.id), "user": str(self.member.id), "percentage": "20", "role": ""}],
            initial_count=1,
        )
        self.modeladmin.save_formset(self.request, None, formset, change=True)
        self.assertEqual(set(participant.monthly_assignments.values_list("percentage", flat=True)), {20})

    def test_delete_removes_remaining_months(self):
        participant = self._participant(percentage=40)
        create_remaining_assignments(participant, self.github_manager)
        formset = self._formset(
            [
                {
                    "id": str(participant.id),
                    "project": str(self.project.id),
                    "user": str(self.member.id),
                    "percentage": "40",
                    "role": "",
                    "DELETE": "on",
                }
            ],
            initial_count=1,
        )
        self.modeladmin.save_formset(self.request, None, formset, change=True)
        self.assertFalse(ProjectParticipant.objects.filter(pk=participant.pk).exists())
        self.assertFalse(ProjectMonthlyAssignment.objects.filter(project=self.project, user=self.member).exists())

    def test_monthly_assignments_link_filters_by_participant(self):
        participant = self._participant()
        create_remaining_assignments(participant, self.github_manager)
        link = self.inline.monthly_assignments_link(participant)
        self.assertIn(f"/projectmonthlyassignment/?participant={participant.pk}", link)
        self.assertIn("3", link)


class BackfillParticipantsMigrationTestCase(ParticipantTestBase):
    """0057 backfill: one participant per existing (project, user) with the latest month's defaults; rows linked."""

    def test_backfill_creates_participants_from_latest_month(self):
        backfill = importlib.import_module("projects.migrations.0057_project_participant").backfill_participants
        ProjectMonthlyAssignment.objects.bulk_create(  # bulk_create: pre-migration rows have no participant
            [
                ProjectMonthlyAssignment(project=self.project, user=self.member, month=datetime.date(2026, 3, 1), percentage=20),
                ProjectMonthlyAssignment(
                    project=self.project, user=self.member, month=datetime.date(2026, 5, 1), percentage=60, role=ProjectRoles.TESTER.value
                ),
                ProjectMonthlyAssignment(project=self.project, user=self.user, month=datetime.date(2026, 4, 1), percentage=30),
            ]
        )

        backfill(django_apps, None)

        participants = {p.user_id: p for p in ProjectParticipant.objects.filter(project=self.project)}
        self.assertEqual(set(participants), {self.member.id, self.user.id})
        member_participant = participants[self.member.id]
        self.assertEqual((member_participant.percentage, member_participant.role), (60, ProjectRoles.TESTER.value))
        self.assertFalse(ProjectMonthlyAssignment.objects.filter(project=self.project, participant__isnull=True).exists())
