"""Regression: deleting a KippoProject must cascade to its weekly-effort and monthly-assignment
rows instead of raising an IntegrityError.

Previously both `project` FKs used `on_delete=models.DO_NOTHING`, so deleting a project that had
any ProjectWeeklyEffort / ProjectMonthlyAssignment rows violated the database FK constraint at
COMMIT and surfaced as an admin HTTP 500 (delete_selected -> queryset.delete()).
"""

import datetime
import uuid

from accounts.models import KippoUser
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase

from projects.models import KippoProject, ProjectMonthlyAssignment, ProjectWeeklyEffort


class KippoProjectDeleteCascadeTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        self.project.start_date = datetime.date(2026, 3, 15)
        self.project.save()
        ProjectMonthlyAssignment.objects.create(
            project=self.project,
            user=self.user,
            month=datetime.date(2026, 4, 1),
            percentage=50,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=datetime.date(2026, 4, 6),
            hours=10,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def _assert_project_and_children_gone(self, project_id: uuid.UUID) -> None:
        self.assertFalse(KippoProject.objects.filter(id=project_id).exists())
        self.assertFalse(ProjectMonthlyAssignment.objects.filter(project_id=project_id).exists())
        self.assertFalse(ProjectWeeklyEffort.objects.filter(project_id=project_id).exists())

    def test_instance_delete_cascades_to_effort_and_assignments(self):
        project_id = self.project.id
        self.project.delete()  # previously raised IntegrityError
        self._assert_project_and_children_gone(project_id)

    def test_queryset_delete_cascades(self):
        # mirrors the admin delete_selected path (queryset.delete())
        project_id = self.project.id
        KippoProject.objects.filter(id=project_id).delete()
        self._assert_project_and_children_gone(project_id)
