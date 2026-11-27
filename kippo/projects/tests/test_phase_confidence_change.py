"""KippoProject フェーズ / 確度 change history — kiconiaworks/kippo#77."""

from http import HTTPStatus
from unittest.mock import MagicMock

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase, setup_basic_project
from django.conf import settings
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from projects.admin import (
    CLOSE_PROJECT_NO_CONTINUATION_VALUE,
    KippoProjectAdmin,
    KippoProjectPhaseConfidenceChangeInline,
    KippoProjectStatusReadOnlyInine,
)
from projects.models import PHASE_COMPLETED, PHASE_CONFIDENCE, KippoProject, KippoProjectPhaseConfidenceChange

NEW_PHASE = "proposing-high"
MANUAL_CONFIDENCE = 55


class PhaseConfidenceChangeModelTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.project = KippoProject.objects.get(pk=created["KippoProject"].pk)  # loaded via from_db
        self.editor = KippoUser.objects.create(username="editor", email="editor@example.com")

    def test_create_is_not_logged(self):
        self.assertFalse(KippoProjectPhaseConfidenceChange.objects.filter(project=self.project).exists())

    def test_phase_change_logs_phase_and_derived_confidence(self):
        previous_phase = self.project.phase
        previous_confidence = self.project.confidence
        self.project.phase = NEW_PHASE
        self.project.updated_by = self.editor
        self.project.save()

        change = KippoProjectPhaseConfidenceChange.objects.get(project=self.project)
        self.assertEqual(change.previous_phase, previous_phase)
        self.assertEqual(change.new_phase, NEW_PHASE)
        self.assertEqual(change.previous_confidence, previous_confidence)
        self.assertEqual(change.new_confidence, PHASE_CONFIDENCE[NEW_PHASE])
        self.assertEqual(change.changed_by, self.editor)
        self.assertIsNotNone(change.changed_datetime)

    def test_save_without_phase_or_confidence_change_is_not_logged(self):
        self.project.name = "renamed project"
        self.project.save()
        self.assertFalse(KippoProjectPhaseConfidenceChange.objects.filter(project=self.project).exists())

    def test_partial_save_skipping_phase_and_confidence_is_not_logged(self):
        self.project.phase = NEW_PHASE  # not written: update_fields excludes phase
        self.project.save(update_fields=["name"])
        self.assertFalse(KippoProjectPhaseConfidenceChange.objects.filter(project=self.project).exists())

    def test_consecutive_changes_log_one_row_each(self):
        self.project.confidence = MANUAL_CONFIDENCE
        self.project.save()
        self.project.phase = NEW_PHASE
        self.project.save()

        changes = list(KippoProjectPhaseConfidenceChange.objects.filter(project=self.project).order_by("changed_datetime"))
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0].new_confidence, MANUAL_CONFIDENCE)
        self.assertEqual(changes[1].previous_confidence, MANUAL_CONFIDENCE)  # snapshot refreshed after save
        self.assertEqual(changes[1].new_confidence, PHASE_CONFIDENCE[NEW_PHASE])


class PhaseConfidenceChangeApiTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_confidence_override_via_api_is_logged_with_requesting_user(self):
        previous_phase = self.project.phase
        previous_confidence = self.project.confidence
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.patch(url, {"confidence": MANUAL_CONFIDENCE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)

        change = KippoProjectPhaseConfidenceChange.objects.get(project=self.project)
        self.assertEqual(change.previous_phase, previous_phase)
        self.assertEqual(change.new_phase, previous_phase)
        self.assertEqual(change.previous_confidence, previous_confidence)
        self.assertEqual(change.new_confidence, MANUAL_CONFIDENCE)
        self.assertEqual(change.changed_by, self.user)


class PhaseConfidenceChangeAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        created = setup_basic_project(organization=self.organization)
        self.project = created["KippoProject"]
        OrganizationMembership.objects.create(
            user=self.superuser_no_org,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_login(self.superuser_no_org)

    def test_admin_save_model_logs_requesting_user(self):
        model_admin = KippoProjectAdmin(KippoProject, AdminSite())
        request = RequestFactory().post("/")
        request.user = self.superuser_no_org
        form = MagicMock(changed_data=["phase"])
        project = KippoProject.objects.get(pk=self.project.pk)
        project.phase = NEW_PHASE
        model_admin.save_model(request, project, form, change=True)

        change = KippoProjectPhaseConfidenceChange.objects.get(project=self.project)
        self.assertEqual(change.new_phase, NEW_PHASE)
        self.assertEqual(change.changed_by, self.superuser_no_org)

    def test_close_action_logs_completed_phase(self):
        response = self.client.post(
            reverse("admin:projects_kippoproject_changelist"),
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project.id)],
                "post": "yes",
                "follow_up": CLOSE_PROJECT_NO_CONTINUATION_VALUE,
                "close_comment": "done",
            },
        )
        self.assertIn(response.status_code, (HTTPStatus.OK, HTTPStatus.FOUND))

        change = KippoProjectPhaseConfidenceChange.objects.get(project=self.project)
        self.assertEqual(change.new_phase, PHASE_COMPLETED)
        self.assertEqual(change.changed_by, self.superuser_no_org)

    def test_inline_sits_directly_above_comments_and_is_collapsed(self):
        inlines = KippoProjectAdmin.inlines
        self.assertEqual(inlines.index(KippoProjectPhaseConfidenceChangeInline) + 1, inlines.index(KippoProjectStatusReadOnlyInine))
        self.assertIn("collapse", KippoProjectPhaseConfidenceChangeInline.classes)
        self.assertIn(KippoProjectPhaseConfidenceChangeInline, KippoProjectAdmin.HIDDEN_ON_ADD_INLINES)

    def test_change_page_renders_history_read_only(self):
        project = KippoProject.objects.get(pk=self.project.pk)
        project.confidence = MANUAL_CONFIDENCE
        project.updated_by = self.superuser_no_org
        project.save()

        response = self.client.get(reverse("admin:projects_kippoproject_change", args=[self.project.pk]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode()
        self.assertIn("フェーズ・確度変更履歴", content)
        self.assertIn(f"% → {MANUAL_CONFIDENCE}%", content)
        inline_formset = next(
            formset for formset in response.context["inline_admin_formsets"] if formset.opts.model is KippoProjectPhaseConfidenceChange
        )
        self.assertFalse(inline_formset.has_add_permission)
        self.assertFalse(inline_formset.has_change_permission)
        self.assertFalse(inline_formset.has_delete_permission)
