"""KippoProject フェーズ / 確度 change history — kiconiaworks/kippo#77."""

from http import HTTPStatus
from unittest.mock import MagicMock

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase, setup_basic_project
from django.conf import settings
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.sites import AdminSite
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
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

    def test_deferred_load_does_not_recurse(self):
        project = KippoProject.objects.only("id").get(pk=self.project.pk)
        self.assertEqual(project.confidence, self.project.confidence)  # lazy load snapshots the DB value
        project.save()
        self.assertFalse(KippoProjectPhaseConfidenceChange.objects.filter(project=self.project).exists())

    def test_organization_cascade_delete_does_not_recurse(self):
        # The deletion Collector loads cascaded projects with .only(<referenced fields>). The org's
        # project categories PROTECT it, so the expected outcome is ProtectedError (not RecursionError).
        with self.assertRaises(ProtectedError):
            self.project.organization.delete()

    def test_confidence_only_partial_save_logs_persisted_phase(self):
        persisted_phase = self.project.phase
        self.project.phase = NEW_PHASE  # in memory only: update_fields writes confidence alone
        self.project.confidence = MANUAL_CONFIDENCE
        self.project.save(update_fields=["confidence"])

        change = KippoProjectPhaseConfidenceChange.objects.get(project=self.project)
        self.assertEqual(change.previous_phase, persisted_phase)
        self.assertEqual(change.new_phase, persisted_phase)
        self.assertEqual(change.new_confidence, MANUAL_CONFIDENCE)

    def test_refresh_from_db_resnapshots(self):
        KippoProject.objects.filter(pk=self.project.pk).update(confidence=MANUAL_CONFIDENCE)  # another writer
        self.project.refresh_from_db()
        self.project.save()
        self.assertFalse(KippoProjectPhaseConfidenceChange.objects.filter(project=self.project).exists())
        self.assertEqual(self.project.confidence, MANUAL_CONFIDENCE)

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


class ProjectResponsePhaseConfidenceChangesTestCase(TestCase):
    """`phase_confidence_changes` in the project API response — kiconiaworks/kippo#79."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.user.first_name = "Taro"
        self.user.last_name = "Yamada"
        self.user.save()
        self.project = KippoProject.objects.get(pk=created["KippoProject"].pk)  # loaded via from_db
        self.initial_phase = self.project.phase
        self.initial_confidence = self.project.confidence
        self.list_url = f"{settings.URL_PREFIX}/api/projects/"
        self.detail_url = f"{self.list_url}{self.project.id}/"
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _change_phase(self, phase: str, project: KippoProject | None = None) -> None:
        project = project or self.project
        project.phase = phase
        project.updated_by = self.user
        project.save()

    def _detail_changes(self) -> list[dict]:
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        return response.json()["phase_confidence_changes"]

    def test_project_without_changes_has_an_empty_list(self):
        self.assertEqual(self._detail_changes(), [])

    def test_detail_lists_changes_newest_first_with_labels_and_user(self):
        self._change_phase(NEW_PHASE)
        self._change_phase(PHASE_COMPLETED)

        changes = self._detail_changes()
        self.assertEqual([change["new_phase"] for change in changes], [PHASE_COMPLETED, NEW_PHASE])

        phase_labels = dict(KippoProject._meta.get_field("phase").choices)
        oldest = changes[-1]
        self.assertEqual(
            {key: value for key, value in oldest.items() if key not in ("id", "changed_datetime")},
            {
                "previous_phase": self.initial_phase,
                "previous_phase_display": str(phase_labels[self.initial_phase]),
                "new_phase": NEW_PHASE,
                "new_phase_display": str(phase_labels[NEW_PHASE]),
                "previous_confidence": self.initial_confidence,
                "new_confidence": PHASE_CONFIDENCE[NEW_PHASE],
                "changed_by_username": self.user.username,
                "changed_by_display_name": "Taro Yamada",
            },
        )
        self.assertIsNotNone(oldest["id"])
        self.assertIsNotNone(oldest["changed_datetime"])

    def test_list_returns_each_projects_own_changes(self):
        other_project = KippoProject.objects.exclude(pk=self.project.pk).filter(organization=self.project.organization).first()
        self.assertIsNotNone(other_project)
        self._change_phase(NEW_PHASE)
        self._change_phase(PHASE_COMPLETED, project=other_project)

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        changes_by_project = {project["id"]: project["phase_confidence_changes"] for project in response.json()["results"]}
        self.assertEqual([change["new_phase"] for change in changes_by_project[str(self.project.id)]], [NEW_PHASE])
        self.assertEqual([change["new_phase"] for change in changes_by_project[str(other_project.id)]], [PHASE_COMPLETED])
        self.assertEqual(changes_by_project[str(self.project.id)], self._detail_changes())

    def test_patch_response_includes_the_change_it_made(self):
        self._change_phase(NEW_PHASE)
        response = self.client.patch(self.detail_url, {"confidence": MANUAL_CONFIDENCE}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        changes = response.json()["phase_confidence_changes"]
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]["previous_confidence"], PHASE_CONFIDENCE[NEW_PHASE])
        self.assertEqual(changes[0]["new_confidence"], MANUAL_CONFIDENCE)
        self.assertEqual(changes, self._detail_changes())

    def test_changes_cannot_be_written_through_the_project(self):
        self._change_phase(NEW_PHASE)
        forged = [{"previous_phase": NEW_PHASE, "new_phase": PHASE_COMPLETED, "previous_confidence": 1, "new_confidence": 2}]
        response = self.client.patch(self.detail_url, {"phase_confidence_changes": forged}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(KippoProjectPhaseConfidenceChange.objects.filter(project=self.project).count(), 1)
        self.assertEqual([change["new_phase"] for change in response.json()["phase_confidence_changes"]], [NEW_PHASE])

    def test_deleted_user_leaves_null_author_fields(self):
        editor = KippoUser.objects.create(username="editor", email="editor@example.com")
        self.project.phase = NEW_PHASE
        self.project.updated_by = editor
        self.project.save()
        editor.delete()

        change = self._detail_changes()[0]
        self.assertIsNone(change["changed_by_username"])
        self.assertIsNone(change["changed_by_display_name"])

    def test_no_separate_endpoint(self):
        response = self.client.get(f"{self.detail_url}phase-confidence-changes/")
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_schema_keeps_the_phase_enum_name(self):
        """The change log shares KippoProject.phase's choices; clients import that enum as `PhaseEnum`."""
        response = self.client.get(f"{settings.URL_PREFIX}/api/schema/", {"format": "json"})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        schemas = response.json()["components"]["schemas"]
        self.assertIn("PhaseEnum", schemas)
        self.assertNotIn("NewPhaseEnum", schemas)
        self.assertNotIn("PreviousPhaseEnum", schemas)
        self.assertIn("phase_confidence_changes", schemas["KippoProject"]["properties"])
        change_properties = schemas["KippoProjectPhaseConfidenceChange"]["properties"]
        for field in ("previous_phase", "new_phase"):
            with self.subTest(field=field):
                self.assertIn("#/components/schemas/PhaseEnum", str(change_properties[field]))

    def _query_count(self, url: str) -> int:
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(url).status_code, HTTPStatus.OK)
        return len(queries)

    def test_query_count_does_not_grow_with_changes(self):
        other_project = KippoProject.objects.exclude(pk=self.project.pk).filter(organization=self.project.organization).first()
        self._change_phase(NEW_PHASE)
        list_queries, detail_queries = self._query_count(self.list_url), self._query_count(self.detail_url)
        for phase in (PHASE_COMPLETED, NEW_PHASE, PHASE_COMPLETED):
            self._change_phase(phase)
            self._change_phase(phase, project=other_project)
        self.assertEqual(self._query_count(self.list_url), list_queries)
        self.assertEqual(self._query_count(self.detail_url), detail_queries)


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
