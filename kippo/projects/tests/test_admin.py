import datetime
from datetime import date
from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from octocat.models import GithubRepository

    from projects.admin import GithubRepositoryProjectInlineForm

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase
from django import forms
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME, AdminForm
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone

from projects.admin import (
    ActiveKippoProjectAdmin,
    KippoCustomerAdmin,
    KippoMilestoneAdmin,
    KippoProjectAdmin,
    KippoProjectAdminForm,
    ProjectAssignmentRateInline,
    ProjectWeeklyEffortAdminInline,
    _next_upsell_project_name,
    _start_of_next_month,
)
from projects.models import ActiveKippoProject, KippoCustomer, KippoMilestone, KippoProject, KippoProjectStatus, ProjectColumnSet


class MockRequest:
    GET = {}
    POST = {}
    path = ""
    _messages = MagicMock()

    def __init__(self, *args, **kwargs) -> None:
        self.GET = {}
        self.POST = {}
        self._messages = MagicMock()

    def get_full_path(self):
        return self.path


class IsStaffOrganizationKippoProjectAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()

        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name="project1",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone1 = KippoMilestone.objects.create(
            project=self.project1,
            title="milestone1",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.project2 = KippoProject.objects.create(
            organization=self.other_organization,
            name="project2",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone2 = KippoMilestone.objects.create(
            project=self.project2,
            title="milestone2",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.organization_usera = KippoUser.objects.create(username="organization_usera")
        OrganizationMembership.objects.create(organization=self.organization, user=self.organization_usera)
        self.organization_users = OrganizationMembership.objects.filter(organization=self.organization).values_list("user", flat=True)

        other_organization_usera = KippoUser.objects.create(username="other_organization_usera")
        OrganizationMembership.objects.create(organization=self.organization, user=other_organization_usera)

    def test_list_objects(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # superuser should list all tasks
        all_tasks_count = KippoProject.objects.count()
        self.assertTrue(all_tasks_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_results = list(qs)
        expected_count = KippoProject.objects.filter(organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(len(queryset_results) == expected_count, f"actual({len(queryset_results)}) != expected({expected_count})")
        for result in queryset_results:
            self.assertTrue(result.organization == self.organization)

    def test_projectweeklyeffort_inlineadmin(self):
        assert KippoUser.objects.all().count() > self.organization_users.count()

        # modeladmin = KippoProjectAdmin(KippoProject, self.site)
        orguser_request = MockRequest()
        orguser_request.user = self.organization_usera
        inline = ProjectWeeklyEffortAdminInline(parent_model=KippoProject, admin_site=self.site)
        formset = inline.get_formset(request=orguser_request, obj=self.project1)
        # check project form users
        # -- compare user ids
        expected = set(self.organization_users)
        actual = set(u.id for u in formset.form.base_fields["user"].queryset)
        self.assertEqual(actual, expected)


class IsStaffOrganizationKippoMilestoneAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()

        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name="project1",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone1 = KippoMilestone.objects.create(
            project=self.project1,
            title="milestone1",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.project2 = KippoProject.objects.create(
            organization=self.other_organization,
            name="project2",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone2 = KippoMilestone.objects.create(
            project=self.project2,
            title="milestone2",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_list_objects(self):
        modeladmin = KippoMilestoneAdmin(KippoMilestone, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # superuser should list all tasks
        all_tasks_count = KippoProject.objects.count()
        self.assertTrue(all_tasks_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_results = list(qs)
        expected_count = KippoProject.objects.filter(organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(len(queryset_results) == expected_count, f"actual({len(queryset_results)}) != expected({expected_count})")
        for result in queryset_results:
            self.assertTrue(result.project.organization == self.organization)


class ProjectsAdminViewTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()

        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name="project1",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone1 = KippoMilestone.objects.create(
            project=self.project1,
            title="milestone1",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.project2 = KippoProject.objects.create(
            organization=self.other_organization,
            name="project2",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone2 = KippoMilestone.objects.create(
            project=self.project2,
            title="milestone2",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_kippomilestone_view(self):
        url = reverse("admin:projects_kippomilestone_changelist")
        self.client.force_login(self.superuser_no_org)
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "admin/change_list.html")

        # self.client.force_login(self.client_user)
        # response = self.client.get(url)
        # self.assertEqual(response.status_code, HTTPStatus.OK)
        # self.assertTemplateUsed(response, "admin/change_list.html")


class CloseProjectActionTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()
        # add organization membership for the superuser to satisfy has_add_permission
        OrganizationMembership.objects.create(
            user=self.superuser_no_org,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name="project1",
            category="poc",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.project2 = KippoProject.objects.create(
            organization=self.organization,
            name="project2",
            category="poc",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.changelist_url = reverse("admin:projects_kippoproject_changelist")
        self.client.force_login(self.superuser_no_org)

    def test_multiple_projects_selected_rejected(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id), str(self.project2.id)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.project1.refresh_from_db()
        self.project2.refresh_from_db()
        self.assertFalse(self.project1.is_closed)
        self.assertFalse(self.project2.is_closed)
        # ensure error message displayed
        messages_list = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("exactly one" in m.lower() for m in messages_list), messages_list)

    def test_already_closed_rejected(self):
        self.project1.is_closed = True
        self.project1.save()
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        messages_list = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("re-open" in m.lower() for m in messages_list), messages_list)

    def test_no_upsell_requires_close_comment(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
                "post": "yes",
                "category": "__no_upsell__",
                "close_comment": "",
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        # form was redisplayed with errors; project not closed
        self.project1.refresh_from_db()
        self.assertFalse(self.project1.is_closed)
        # form should have errors attribute
        form = response.context.get("form")
        self.assertIsNotNone(form)
        self.assertIn("close_comment", form.errors)

    def test_no_upsell_flow_closes_project(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
                "post": "yes",
                "category": "__no_upsell__",
                "close_comment": "Project completed successfully.",
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertIn("/admin/projects/kippoproject/", response["Location"])
        # ensure no add page is requested for upsell-none
        self.assertNotIn("/add/", response["Location"])

        self.project1.refresh_from_db()
        self.assertTrue(self.project1.is_closed)
        self.assertEqual(self.project1.actual_date, timezone.now().date())
        self.assertFalse(self.project1.display_as_active)
        self.assertFalse(self.project1.display_in_project_report)
        self.assertEqual(self.project1.close_comment, "Project completed successfully.")
        self.assertIsNotNone(self.project1.closed_datetime)

        # confirm no new project was created
        self.assertEqual(KippoProject.objects.count(), 2)

    def test_upsell_flow_closes_and_redirects(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
                "post": "yes",
                "category": "upsell-improvement",
                "close_comment": "",
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertIn("/admin/projects/kippoproject/add/", response["Location"])
        parsed = urlparse(response["Location"])
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("category"), ["upsell-improvement"])
        self.assertEqual(params.get("parent_project"), [str(self.project1.id)])
        # close-action upsell redirect must include the parent's organization and the upsell marker
        # so the add form can derive the org server-side and detect the entry point.
        self.assertEqual(params.get("organization"), [str(self.project1.organization_id)])
        self.assertEqual(params.get("_upsell_source"), ["close"])

        self.project1.refresh_from_db()
        self.assertTrue(self.project1.is_closed)
        self.assertFalse(self.project1.display_as_active)
        self.assertFalse(self.project1.display_in_project_report)
        # category on the closing project is unchanged
        self.assertEqual(self.project1.category, "poc")

    def test_upsell_redirect_prefills_new_project_fields(self):
        # populate source-project fields that should propagate to the upsell child
        self.project1.slack_channel_name = "proj1"
        self.project1.slack_notification_channel_name = "proj1-notify"
        self.project1.document_folder_url = "https://docs.example.com/proj1"
        self.project1.github_project_html_url = "https://github.com/orgs/example/projects/42"
        self.project1.github_project_api_nodeid = "PVT_kwDO_NODE_ID"
        self.project1.docbase_tag = "proj1-tag"
        self.project1.save()

        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
                "post": "yes",
                "category": "upsell-new-proposal",
                "close_comment": "",
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        params = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(params.get("name"), ["project1 Phase 2"])
        self.assertEqual(params.get("slack_channel_name"), ["proj1"])
        self.assertEqual(params.get("slack_notification_channel_name"), ["proj1-notify"])
        self.assertEqual(params.get("document_folder_url"), ["https://docs.example.com/proj1"])
        self.assertEqual(params.get("github_project_html_url"), ["https://github.com/orgs/example/projects/42"])
        self.assertEqual(params.get("github_project_api_nodeid"), ["PVT_kwDO_NODE_ID"])
        self.assertEqual(params.get("columnset"), [str(self.project1.columnset_id)])
        self.assertEqual(params.get("docbase_tag"), ["proj1-tag"])
        # start_date should be the first day of the month following today
        today = timezone.now().date()
        expected_start = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        self.assertEqual(params.get("start_date"), [expected_start.isoformat()])

    def test_upsell_redirect_omits_blank_source_fields(self):
        # leave optional source fields blank: prefill params should not include empty values
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
                "post": "yes",
                "category": "upsell-improvement",
                "close_comment": "",
            },
        )
        params = parse_qs(urlparse(response["Location"]).query)
        for blank_field in (
            "slack_channel_name",
            "slack_notification_channel_name",
            "document_folder_url",
            "github_project_html_url",
            "github_project_api_nodeid",
            "docbase_tag",
        ):
            with self.subTest(field=blank_field):
                self.assertNotIn(blank_field, params)

    def test_close_form_category_dropdown_default_is_no_upsell(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        form = response.context["form"]
        self.assertEqual(form.fields["category"].widget.__class__.__name__, "Select")
        self.assertEqual(form.fields["category"].initial, "__no_upsell__")

    def test_add_form_hides_close_comment_field(self):
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertNotIn('name="close_comment"', response.content.decode())

    def test_intermediate_form_get(self):
        """Without 'post=yes', the action displays the intermediate form."""
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "admin/projects/close_project_action.html")
        self.assertContains(response, "project1")

    def _assert_parent_project_selectable(self, adminform: AdminForm, response_content: str) -> None:
        widget = adminform.form.fields["parent_project"].widget
        # the admin FK widget is wrapped in RelatedFieldWidgetWrapper — unwrap to get the real widget
        inner_widget = getattr(widget, "widget", widget)
        self.assertNotIsInstance(inner_widget, forms.HiddenInput)
        self.assertIn('<select name="parent_project"', response_content)

    def test_add_form_prefills_from_get_params(self):
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url, {"category": "upsell-improvement", "parent_project": str(self.project1.id)})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertEqual(adminform.form.initial.get("category"), "upsell-improvement")
        self.assertEqual(adminform.form.initial.get("parent_project"), str(self.project1.id))
        # parent_project should be a selectable field on add (not hidden) so manual users can pick a parent
        self._assert_parent_project_selectable(adminform, response.content.decode())

    def test_close_related_fields_hidden_from_change_form(self):
        url = reverse("admin:projects_kippoproject_change", args=[self.project1.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode()
        for field_name in ("is_closed", "actual_date", "display_as_active", "display_in_project_report"):
            self.assertNotIn(f'name="{field_name}"', content, f"{field_name} should not be in form")

    def test_change_form_shows_parent_project_readonly(self):
        child = KippoProject.objects.create(
            organization=self.organization,
            name="upsell-child",
            category="upsell-improvement",
            columnset=ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK),
            parent_project=self.project1,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        url = reverse("admin:projects_kippoproject_change", args=[child.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        # parent_project should appear as readonly (no input element with that name)
        self.assertNotIn('name="parent_project"', response.content.decode())
        self.assertContains(response, self.project1.name)

    def test_manual_add_form_exposes_parent_project_as_selectable(self):
        # manual add (no GET params from close-action) — parent_project should be a visible Select, not hidden
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertIn("parent_project", adminform.form.fields)
        self._assert_parent_project_selectable(adminform, response.content.decode())
        # available parent options include the existing project
        self.assertContains(response, self.project1.name)

    def test_add_form_parent_project_queryset_scoped_to_new_projects_organization(self):
        # parent_project dropdown should only list projects from the same org as the new project being created
        other_org_project = KippoProject.objects.create(
            organization=self.other_organization,
            name="other-org-project",
            category="poc",
            columnset=ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK),
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # log in as a staff user who only belongs to self.organization (not self.other_organization)
        self.client.force_login(self.staffuser_with_org)
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        parent_qs = response.context["adminform"].form.fields["parent_project"].queryset
        parent_ids = set(parent_qs.values_list("id", flat=True))
        self.assertIn(self.project1.id, parent_ids)
        self.assertNotIn(other_org_project.id, parent_ids)

    def test_add_form_parent_project_queryset_excludes_other_orgs_for_superuser(self):
        # superuser: parent_project queryset is scoped to the new project's org, not globally visible
        other_org_project = KippoProject.objects.create(
            organization=self.other_organization,
            name="other-org-project-superuser-view",
            category="poc",
            columnset=ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK),
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # superuser_no_org was added to self.organization in setUp; log in
        self.client.force_login(self.superuser_no_org)
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        parent_qs = response.context["adminform"].form.fields["parent_project"].queryset
        parent_ids = set(parent_qs.values_list("id", flat=True))
        self.assertIn(self.project1.id, parent_ids)
        self.assertNotIn(other_org_project.id, parent_ids)

    def test_upsell_redirect_hides_parent_project_and_organization(self):
        # close-action upsell redirect: parent_project and organization must render as hidden inputs
        # so the user cannot edit them; the values still POST so the existing validator runs.
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(
            url,
            {
                "_upsell_source": "close",
                "category": "upsell-improvement",
                "parent_project": str(self.project1.id),
                "organization": str(self.project1.organization_id),
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        for field_name in ("parent_project", "organization"):
            with self.subTest(field=field_name):
                widget = adminform.form.fields[field_name].widget
                inner_widget = getattr(widget, "widget", widget)
                self.assertIsInstance(inner_widget, forms.HiddenInput)
        content = response.content.decode()
        self.assertIn('type="hidden" name="parent_project"', content)
        self.assertIn('type="hidden" name="organization"', content)

    def test_manual_add_without_upsell_marker_keeps_organization_visible(self):
        # manual add (no _upsell_source marker) — organization should NOT be forced hidden by the
        # upsell branch. (When the user has multiple orgs, organization is shown as a visible select.)
        url = reverse("admin:projects_kippoproject_add")
        # GET with parent_project but no _upsell_source: must remain in manual-add mode
        response = self.client.get(url, {"category": "upsell-improvement", "parent_project": str(self.project1.id)})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        # parent_project remains selectable (not hidden)
        self._assert_parent_project_selectable(adminform, response.content.decode())

    def test_upsell_form_with_derived_organization_is_valid_and_saves_parent(self):
        # the upsell prefill always sets organization = parent_project.organization, so the form
        # validator's parent-org invariant is satisfied by construction. Save and verify the result.
        form = KippoProjectAdminForm(
            data={
                "organization": str(self.project1.organization_id),
                "parent_project": str(self.project1.id),
                "name": "project1 Phase 2",
                "phase": "lead-evaluation",
                "confidence": "80",
                "category": "upsell-improvement",
                "columnset": str(self.project1.columnset_id),
                "start_date": self.current_date.isoformat(),
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        new_project = form.save(commit=False)
        new_project.created_by = self.superuser_no_org
        new_project.updated_by = self.superuser_no_org
        new_project.save()
        self.assertEqual(new_project.parent_project_id, self.project1.id)
        self.assertEqual(new_project.organization_id, self.project1.organization_id)

    def test_upsell_form_with_mismatched_organization_is_rejected(self):
        # tampered POST: hidden organization differs from parent_project.organization — the existing
        # validator must catch the mismatch (no validator changes were made for this issue).
        form = KippoProjectAdminForm(
            data={
                # parent is in self.organization, but the submitted organization is the other org
                "organization": str(self.other_organization.id),
                "parent_project": str(self.project1.id),
                "name": "tampered-project",
                "phase": "lead-evaluation",
                "confidence": "80",
                "category": "upsell-improvement",
                "columnset": str(self.project1.columnset_id),
                "start_date": self.current_date.isoformat(),
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("parent_project", form.errors)


class KippoProjectAdminFormValidationTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        self.columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()
        self.parent = KippoProject.objects.create(
            organization=self.organization,
            name="parent-project",
            category="poc",
            columnset=self.columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def _form_data(self, *, category: str, parent_project_id: str | None = None) -> dict:
        data = {
            "organization": str(self.organization.id),
            "name": "manual-new-project",
            "phase": "lead-evaluation",
            "confidence": "80",
            "category": category,
            "columnset": str(self.columnset.pk),
            "start_date": self.current_date.isoformat(),
        }
        if parent_project_id:
            data["parent_project"] = parent_project_id
        return data

    def test_upsell_category_without_parent_project_is_invalid(self):
        form = KippoProjectAdminForm(data=self._form_data(category="upsell-improvement"))
        self.assertFalse(form.is_valid())
        self.assertIn("parent_project", form.errors)

    def test_upsell_category_with_parent_project_is_valid(self):
        form = KippoProjectAdminForm(
            data=self._form_data(category="upsell-improvement", parent_project_id=str(self.parent.id)),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_non_upsell_category_does_not_require_parent_project(self):
        form = KippoProjectAdminForm(data=self._form_data(category="poc"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_change_form_with_upsell_category_uses_persisted_parent_when_field_omitted(self):
        # change form: parent_project is readonly so it isn't submitted in POST data;
        # validation must fall back to the persisted instance value to avoid a false-positive error.
        existing_upsell = KippoProject.objects.create(
            organization=self.organization,
            name="existing-upsell",
            category="upsell-improvement",
            columnset=self.columnset,
            parent_project=self.parent,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        form = KippoProjectAdminForm(
            instance=existing_upsell,
            data=self._form_data(category="upsell-improvement"),  # no parent_project key
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_cross_org_parent_project_submission_is_invalid(self):
        # parent_project from a different organization than the submitted org should be rejected
        cross_org_parent = KippoProject.objects.create(
            organization=self.other_organization,
            name="cross-org-parent",
            category="poc",
            columnset=self.columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        form = KippoProjectAdminForm(
            data=self._form_data(category="upsell-improvement", parent_project_id=str(cross_org_parent.id)),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("parent_project", form.errors)


class UpsellPrefillHelpersTestCase(TestCase):
    def test_next_upsell_project_name_appends_phase_2_when_no_existing_suffix(self):
        self.assertEqual(_next_upsell_project_name("Foo"), "Foo Phase 2")
        self.assertEqual(_next_upsell_project_name("Foo Bar"), "Foo Bar Phase 2")

    def test_next_upsell_project_name_increments_existing_phase_number(self):
        self.assertEqual(_next_upsell_project_name("Foo Phase 2"), "Foo Phase 3")
        self.assertEqual(_next_upsell_project_name("Foo Phase 9"), "Foo Phase 10")
        self.assertEqual(_next_upsell_project_name("Foo Bar Phase 11"), "Foo Bar Phase 12")

    def test_start_of_next_month(self):
        self.assertEqual(_start_of_next_month(datetime.date(2026, 1, 15)), datetime.date(2026, 2, 1))
        self.assertEqual(_start_of_next_month(datetime.date(2026, 12, 31)), datetime.date(2027, 1, 1))
        # boundary: first of month
        self.assertEqual(_start_of_next_month(datetime.date(2026, 4, 1)), datetime.date(2026, 5, 1))


class KippoProjectAdminFixtureTestCaseBase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        self.columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()
        OrganizationMembership.objects.create(
            user=self.superuser_no_org,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_login(self.superuser_no_org)

    def make_project(self, name: str, *, is_closed: bool = False, actual_date: date | None = None) -> KippoProject:
        project = KippoProject.objects.create(
            organization=self.organization,
            name=name,
            category="poc",
            columnset=self.columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        if is_closed:
            project.is_closed = True
            project.actual_date = actual_date
            project.save()
        return project


class ActiveKippoProjectChangeViewTestCase(KippoProjectAdminFixtureTestCaseBase):
    """Regression: ActiveKippoProject.fieldsets must not reference fields excluded by the parent admin."""

    def setUp(self):
        super().setUp()
        self.active_project = self.make_project("active-project")

    def test_change_view_renders_without_keyerror(self):
        url = reverse("admin:projects_activekippoproject_change", args=[self.active_project.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_fieldsets_do_not_reference_excluded_fields(self):
        from projects.admin import ActiveKippoProjectAdmin

        excluded = set(ActiveKippoProjectAdmin.exclude or ())
        for _label, opts in ActiveKippoProjectAdmin.fieldsets:
            overlap = [f for f in opts["fields"] if f in excluded]
            self.assertFalse(overlap, f"fieldset references excluded fields: {overlap}")


class ClosedProjectReadonlyTestCase(KippoProjectAdminFixtureTestCaseBase):
    def setUp(self):
        super().setUp()
        self.open_project = self.make_project("open-project")
        self.closed_project = self.make_project("closed-project", is_closed=True)

    def test_closed_project_change_form_locks_all_editable_fields(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        readonly = set(modeladmin.get_readonly_fields(self.super_user_request, self.closed_project))
        excluded = set(modeladmin.exclude or ())
        expected_locked = {
            f.name for f in KippoProject._meta.get_fields() if getattr(f, "editable", False) and not f.auto_created and f.name not in excluded
        }
        missing = expected_locked - readonly
        self.assertFalse(missing, f"expected fields locked but were not: {missing}")

    def test_open_project_change_form_keeps_fields_editable(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        readonly = set(modeladmin.get_readonly_fields(self.super_user_request, self.open_project))
        editable_field_names = {
            f.name for f in KippoProject._meta.get_fields() if getattr(f, "editable", False) and not f.auto_created and f.name != "parent_project"
        }
        self.assertFalse(editable_field_names & readonly, f"editable fields unexpectedly locked: {editable_field_names & readonly}")

    def test_closed_project_change_form_renders_no_editable_inputs_for_model_fields(self):
        url = reverse("admin:projects_kippoproject_change", args=[self.closed_project.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode()
        for field_name in ("name", "phase", "category", "confidence", "project_manager"):
            self.assertNotIn(f'name="{field_name}"', content, f"{field_name} should be readonly on closed project")


class ReopenProjectActionTestCase(KippoProjectAdminFixtureTestCaseBase):
    def setUp(self):
        super().setUp()
        self.closed_project_a = self.make_project("closed-a", is_closed=True, actual_date=self.current_date)
        self.closed_project_b = self.make_project("closed-b", is_closed=True, actual_date=self.current_date)
        self.open_project = self.make_project("still-open")
        self.changelist_url = reverse("admin:projects_kippoproject_changelist")

    def test_reopen_action_reopens_single_project_and_creates_status_comment(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "reopen_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.closed_project_a.id)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.closed_project_a.refresh_from_db()
        self.assertFalse(self.closed_project_a.is_closed)
        self.assertIsNone(self.closed_project_a.closed_datetime)
        self.assertIsNone(self.closed_project_a.actual_date)
        self.assertEqual(self.closed_project_a.updated_by, self.superuser_no_org)

        statuses = KippoProjectStatus.objects.filter(project=self.closed_project_a)
        self.assertEqual(statuses.count(), 1)
        status = statuses.get()
        self.assertEqual(status.comment, f"re-opened by {self.superuser_no_org.username}")
        self.assertEqual(status.created_by, self.superuser_no_org)

    def test_reopen_action_supports_multiple_projects(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "reopen_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.closed_project_a.id), str(self.closed_project_b.id)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.closed_project_a.refresh_from_db()
        self.closed_project_b.refresh_from_db()
        self.assertFalse(self.closed_project_a.is_closed)
        self.assertFalse(self.closed_project_b.is_closed)
        self.assertEqual(KippoProjectStatus.objects.filter(project=self.closed_project_a).count(), 1)
        self.assertEqual(KippoProjectStatus.objects.filter(project=self.closed_project_b).count(), 1)

    def test_reopen_action_skips_already_open_projects(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "reopen_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.closed_project_a.id), str(self.open_project.id)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.closed_project_a.refresh_from_db()
        self.open_project.refresh_from_db()
        self.assertFalse(self.closed_project_a.is_closed)
        self.assertFalse(self.open_project.is_closed)
        # only the originally-closed project should have a re-open status comment
        self.assertEqual(KippoProjectStatus.objects.filter(project=self.closed_project_a).count(), 1)
        self.assertEqual(KippoProjectStatus.objects.filter(project=self.open_project).count(), 0)
        messages_list = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("skipped" in m.lower() for m in messages_list), messages_list)

    def test_reopen_action_rejects_when_no_closed_projects_selected(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "reopen_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.open_project.id)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(KippoProjectStatus.objects.filter(project=self.open_project).count(), 0)
        messages_list = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("no closed projects" in m.lower() for m in messages_list), messages_list)


class KippoProjectAdminInlinesTestCase(KippoProjectAdminFixtureTestCaseBase):
    """Status & weekly-effort inlines should be hidden on /add/ but present on /change/."""

    def setUp(self):
        super().setUp()
        self.project = self.make_project("inline-test-project")

    def test_add_view_hides_status_and_weeklyeffort_inlines(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        inlines = modeladmin.get_inlines(self.staff_user_request, obj=None)
        for cls in KippoProjectAdmin.HIDDEN_ON_ADD_INLINES:
            self.assertNotIn(cls, inlines, f"{cls.__name__} should be hidden on add")

    def test_change_view_shows_status_and_weeklyeffort_inlines(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        inlines = modeladmin.get_inlines(self.staff_user_request, obj=self.project)
        for cls in KippoProjectAdmin.HIDDEN_ON_ADD_INLINES:
            self.assertIn(cls, inlines, f"{cls.__name__} should be present on change")

    def test_activekippoproject_add_view_hides_status_and_weeklyeffort_inlines(self):
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        inlines = modeladmin.get_inlines(self.staff_user_request, obj=None)
        for cls in KippoProjectAdmin.HIDDEN_ON_ADD_INLINES:
            self.assertNotIn(cls, inlines, f"{cls.__name__} should be hidden on ActiveKippoProject add")

    def test_activekippoproject_change_view_shows_status_and_weeklyeffort_inlines(self):
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        inlines = modeladmin.get_inlines(self.staff_user_request, obj=self.project)
        for cls in KippoProjectAdmin.HIDDEN_ON_ADD_INLINES:
            self.assertIn(cls, inlines, f"{cls.__name__} should be present on ActiveKippoProject change")

    def test_add_view_keeps_assignment_rate_and_repository_inlines(self):
        from projects.admin import GithubRepositoryProjectInline

        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        inlines = modeladmin.get_inlines(self.staff_user_request, obj=None)
        self.assertIn(ProjectAssignmentRateInline, inlines)
        self.assertIn(GithubRepositoryProjectInline, inlines)


class ProjectAssignmentRateInlineConfigTestCase(TestCase):
    def test_max_num_is_10(self):
        self.assertEqual(ProjectAssignmentRateInline.max_num, 10)


class ActiveKippoProjectAdminParentProjectFieldTestCase(KippoProjectAdminFixtureTestCaseBase):
    """parent_project should appear in Details fieldset (after category) on add — not on change."""

    def setUp(self):
        super().setUp()
        self.existing_project = self.make_project("existing-project")

    @staticmethod
    def _details_fieldset_fields(fieldsets: list) -> tuple:
        for _label, opts in fieldsets:
            if "category" in opts.get("fields", ()):
                return tuple(opts["fields"])
        raise AssertionError("No fieldset containing 'category' found")

    def test_add_fieldsets_place_parent_project_immediately_after_category(self):
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        fieldsets = modeladmin.get_fieldsets(self.super_user_request, obj=None)
        details_fields = self._details_fieldset_fields(fieldsets)
        self.assertIn("parent_project", details_fields)
        category_index = details_fields.index("category")
        self.assertEqual(details_fields[category_index + 1], "parent_project")

    def test_change_fieldsets_omit_parent_project(self):
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        fieldsets = modeladmin.get_fieldsets(self.super_user_request, obj=self.existing_project)
        details_fields = self._details_fieldset_fields(fieldsets)
        self.assertNotIn("parent_project", details_fields)

    def test_class_fieldsets_attribute_unchanged_after_get_fieldsets(self):
        # get_fieldsets must not mutate the (inherited) class attribute — exclusions are applied
        # to a copy, otherwise mutations would persist across requests.
        from copy import deepcopy

        before = deepcopy(ActiveKippoProjectAdmin.fieldsets)
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        modeladmin.get_fieldsets(self.super_user_request, obj=None)
        modeladmin.get_fieldsets(self.super_user_request, obj=self.existing_project)
        self.assertEqual(ActiveKippoProjectAdmin.fieldsets, before)

    def test_add_view_renders_parent_project_select(self):
        url = reverse("admin:projects_activekippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertIn("parent_project", adminform.form.fields)
        # available parent options include the existing project
        self.assertContains(response, self.existing_project.name)
        # field must be a Select (not hidden) so users can pick a parent
        widget = adminform.form.fields["parent_project"].widget
        inner_widget = getattr(widget, "widget", widget)
        self.assertNotIsInstance(inner_widget, forms.HiddenInput)

    def test_kippoproject_add_view_still_exposes_parent_project_for_close_action_prefill(self):
        # KippoProjectAdmin (the close-action target) must continue to expose parent_project on /add/
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url, {"category": "upsell-improvement", "parent_project": str(self.existing_project.id)})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertIn("parent_project", adminform.form.fields)
        self.assertEqual(adminform.form.initial.get("parent_project"), str(self.existing_project.id))

    @staticmethod
    def _ordered_form_field_names(adminform: AdminForm) -> list:
        # AdminForm iteration yields Fieldsets → Fieldlines → AdminFields/AdminReadonlyFields.
        # Editable fields wrap a BoundField (.field has .name); readonly fields store .field as a dict.
        names = []
        for fieldset in adminform:
            for fieldline in fieldset:
                for admin_field in fieldline:
                    f = admin_field.field
                    names.append(f["name"] if isinstance(f, dict) else f.name)
        return names

    def test_kippoproject_add_view_places_parent_project_immediately_after_category(self):
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ordered = self._ordered_form_field_names(response.context["adminform"])
        self.assertIn("category", ordered)
        self.assertIn("parent_project", ordered)
        self.assertEqual(ordered[ordered.index("category") + 1], "parent_project")

    def test_kippoproject_change_view_places_parent_project_after_category(self):
        # parent_project is statically slotted after `category` in the Details fieldset, so the
        # ordering is consistent across add and change views.
        existing = self.make_project("ordering-change-target")
        url = reverse("admin:projects_kippoproject_change", args=[existing.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ordered = self._ordered_form_field_names(response.context["adminform"])
        self.assertIn("category", ordered)
        self.assertIn("parent_project", ordered)
        self.assertEqual(ordered[ordered.index("category") + 1], "parent_project")


class KippoProjectAdminSingleOrgHidesOrganizationFieldTestCase(KippoProjectAdminFixtureTestCaseBase):
    """On /add/, hide the organization field when the user belongs to exactly one organization."""

    @staticmethod
    def _organization_widget(adminform: AdminForm) -> forms.Widget:
        widget = adminform.form.fields["organization"].widget
        # ModelChoiceField may wrap with RelatedFieldWidgetWrapper — unwrap to inspect the inner widget
        return getattr(widget, "widget", widget)

    def test_add_view_hides_organization_field_for_single_org_user(self):
        # superuser_no_org was added to self.organization in fixture setUp → exactly one org
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertIsInstance(self._organization_widget(adminform), forms.HiddenInput)
        # initial value still preselects the single org
        self.assertEqual(adminform.form.fields["organization"].initial, self.organization)

    def test_add_view_keeps_organization_field_visible_for_multi_org_user(self):
        # promote superuser into a second org so they belong to two organizations
        OrganizationMembership.objects.create(
            user=self.superuser_no_org,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertNotIsInstance(self._organization_widget(adminform), forms.HiddenInput)

    def test_change_view_does_not_hide_organization_field_for_single_org_user(self):
        # On /change/, the hide-on-single-org rule must not apply (organization is fixed but visible)
        existing = self.make_project("single-org-change-target")
        url = reverse("admin:projects_kippoproject_change", args=[existing.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertNotIsInstance(self._organization_widget(adminform), forms.HiddenInput)


class KippoProjectAdminColumnsetFieldTestCase(KippoProjectAdminFixtureTestCaseBase):
    """columnset is required, defaults to first available on /add/, and hidden for non-superusers."""

    @staticmethod
    def _columnset_widget(adminform: AdminForm) -> forms.Widget:
        widget = adminform.form.fields["columnset"].widget
        return getattr(widget, "widget", widget)

    def test_add_view_initial_columnset_is_first_available(self):
        # superuser
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertEqual(adminform.form.fields["columnset"].initial, ProjectColumnSet.objects.first())

    def test_add_view_hides_columnset_field_for_non_superuser(self):
        # log in as staff (single-org); columnset should be HiddenInput
        self.client.force_login(self.staffuser_with_org)
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertIsInstance(self._columnset_widget(adminform), forms.HiddenInput)
        # initial still preselects the first columnset so the form submits successfully
        self.assertEqual(adminform.form.fields["columnset"].initial, ProjectColumnSet.objects.first())

    def test_add_view_keeps_columnset_visible_for_superuser(self):
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertNotIsInstance(self._columnset_widget(adminform), forms.HiddenInput)

    def test_change_view_hides_columnset_field_for_non_superuser(self):
        existing = self.make_project("columnset-change-target")
        self.client.force_login(self.staffuser_with_org)
        url = reverse("admin:projects_kippoproject_change", args=[existing.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertIsInstance(self._columnset_widget(adminform), forms.HiddenInput)


class GithubRepositoryInlineSaveTestCase(KippoProjectAdminFixtureTestCaseBase):
    """Regression: github_repository inline on (Active)KippoProjectAdmin must seed organization
    from the parent project on every code path so GithubRepository.save() never raises
    RelatedObjectDoesNotExist (monkut/kippo#266).
    """

    def setUp(self):
        super().setUp()
        self.project = self.make_project("github-repo-inline-target")

    def _build_form(
        self,
        *,
        data: dict,
        instance: "GithubRepository | None" = None,
        empty_permitted: bool = False,
        with_parent_fk: bool = True,
    ) -> "GithubRepositoryProjectInlineForm":
        from octocat.models import GithubRepository

        from projects.admin import GithubRepositoryProjectInlineForm

        if instance is None:
            instance = GithubRepository()
        if with_parent_fk:
            instance.project_id = self.project.id
        kwargs: dict = {"data": data, "instance": instance}
        if empty_permitted:
            kwargs["empty_permitted"] = True
            kwargs["use_required_attribute"] = False
        return GithubRepositoryProjectInlineForm(**kwargs)

    def test_clean_raises_when_edit_form_clears_url(self):
        from octocat.models import GithubRepository

        # Edit scenario: existing repo whose URL the user has wiped — must surface an
        # error rather than reach GithubRepository.save() with an empty URL.
        existing = GithubRepository(html_url="https://github.com/owner/repo")
        form = self._build_form(data={"html_url": ""}, instance=existing)
        self.assertFalse(form.is_valid())
        self.assertIn("html_url", str(form.errors) + str(form.non_field_errors()))

    def test_empty_extra_form_is_valid_and_does_not_block_parent_save(self):
        # empty_permitted=True mirrors what BaseInlineFormSet sets on extra rows; the
        # combination of unchanged + empty must validate so the parent project saves.
        form = self._build_form(data={"html_url": ""}, empty_permitted=True)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.has_changed())

    def test_save_commit_false_seeds_organization_from_parent_project(self):
        form = self._build_form(data={"html_url": "https://github.com/owner/repo"})
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save(commit=False)
        self.assertEqual(instance.organization_id, self.project.organization_id)
        self.assertEqual(instance.name, "repo")
        self.assertEqual(instance.html_url, "https://github.com/owner/repo")
        self.assertEqual(instance.api_url, "https://api.github.com/repos/owner/repo")

    def test_save_commit_true_persists_repository_with_parent_organization(self):
        from octocat.models import GithubRepository

        form = self._build_form(data={"html_url": "https://github.com/owner/repo"})
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save(commit=True)
        self.assertEqual(saved.organization_id, self.project.organization_id)
        self.assertEqual(saved.project_id, self.project.id)
        from_db = GithubRepository.objects.get(pk=saved.pk)
        self.assertEqual(from_db.organization_id, self.project.organization_id)

    def test_save_adopts_existing_matching_repository_and_links_it_to_project(self):
        from octocat.models import GithubRepository

        # Repos may be pre-created by KippoTask.save(); adopt instead of duplicating.
        existing = GithubRepository.objects.create(
            organization=self.organization,
            name="repo",
            html_url="https://github.com/owner/repo",
            api_url="https://api.github.com/repos/owner/repo",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        form = self._build_form(data={"html_url": "https://github.com/owner/repo"})
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save(commit=True)
        self.assertEqual(saved.pk, existing.pk)
        self.assertEqual(GithubRepository.objects.filter(name="repo").count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.project_id, self.project.id)

    def test_github_repository_save_does_not_raise_on_missing_organization(self):
        """GithubRepository.save() must not raise RelatedObjectDoesNotExist when
        accessing fields that depend on organization — it should rely on the FK
        column (organization_id) and let DB-level NOT NULL surface as IntegrityError
        if no organization was assigned at all.
        """
        from django.db import IntegrityError, transaction
        from octocat.models import GithubRepository

        repo = GithubRepository(
            name="orphan",
            html_url="https://github.com/owner/orphan",
            api_url="https://api.github.com/repos/owner/orphan",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            repo.save()  # may not raise RelatedObjectDoesNotExist before reaching the DB

    def test_change_view_post_with_valid_inline_creates_repository(self):
        """End-to-end: POST the active-project change form with a new github_repository
        inline row containing a valid URL — must succeed (302) and persist a row whose
        organization matches the parent project's organization.
        """
        from octocat.models import GithubRepository

        url = reverse("admin:projects_activekippoproject_change", args=[self.project.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        form_data = _extract_admin_form_post_data(response, self.project)
        prefix = "github_repositories"
        form_data.update(
            {
                f"{prefix}-TOTAL_FORMS": "1",
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "5",
                f"{prefix}-0-html_url": "https://github.com/owner/new-repo",
                f"{prefix}-0-id": "",
                f"{prefix}-0-project": str(self.project.id),
            }
        )
        post_response = self.client.post(url, data=form_data, follow=False)
        self.assertEqual(post_response.status_code, HTTPStatus.FOUND, post_response.content[:500])
        repo = GithubRepository.objects.get(name="new-repo", project=self.project)
        self.assertEqual(repo.organization_id, self.project.organization_id)


def _extract_admin_form_post_data(get_response: HttpResponse, project: KippoProject) -> dict:
    """Build a POST payload for a KippoProject admin change form from a prior GET response.

    Pulls the change form's adminform plus every inline formset's management form so the
    POST mirrors the rendered page; inline rows default to empty (TOTAL=0) and can be
    overridden by the caller.
    """
    adminform = get_response.context["adminform"]
    data: dict = {}
    for name in adminform.form.fields:
        value = adminform.form[name].value()
        if value is None:
            data[name] = ""
        elif hasattr(value, "isoformat"):
            data[name] = value.isoformat()
        else:
            data[name] = str(value)
    # Ensure required fields are present and aligned with the stored project.
    data["name"] = project.name
    data["organization"] = str(project.organization_id)
    data["columnset"] = str(project.columnset_id)
    data["start_date"] = project.start_date.isoformat() if project.start_date else ""
    data["confidence"] = str(project.confidence) if project.confidence is not None else ""

    # Inline management forms: send INITIAL existing rows only, no extras. Including
    # extras with their initial-field values flips has_changed() True on otherwise-empty
    # rows (e.g. projectweeklyeffort_project's auto-populated week_start), which forces
    # required-field validation and blocks the parent save. Callers that want a real
    # extra row override TOTAL_FORMS and supply their own row data.
    for inline_formset in get_response.context["inline_admin_formsets"]:
        formset = inline_formset.formset
        prefix = formset.prefix
        initial_count = formset.initial_form_count()
        data.update(
            {
                f"{prefix}-TOTAL_FORMS": str(initial_count),
                f"{prefix}-INITIAL_FORMS": str(initial_count),
                f"{prefix}-MIN_NUM_FORMS": str(formset.min_num),
                f"{prefix}-MAX_NUM_FORMS": str(formset.max_num),
            }
        )
        for i, form in enumerate(formset.forms[:initial_count]):
            for name in form.fields:
                value = form[name].value()
                if value is None:
                    data[f"{prefix}-{i}-{name}"] = ""
                elif hasattr(value, "isoformat"):
                    data[f"{prefix}-{i}-{name}"] = value.isoformat()
                else:
                    data[f"{prefix}-{i}-{name}"] = str(value)
    return data


class IsStaffOrganizationKippoCustomerAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        # Customers in the user's org and another org
        self.customer_in_org = KippoCustomer.objects.create(
            organization=self.organization,
            name="Acme",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.customer_in_other_org = KippoCustomer.objects.create(
            organization=self.other_organization,
            name="Globex",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_superuser_sees_all_customers(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.customer_in_org.id, ids)
        self.assertIn(self.customer_in_other_org.id, ids)

    def test_staffuser_only_sees_own_org_customers(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        qs = modeladmin.get_queryset(self.staff_user_request)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.customer_in_org.id, ids)
        self.assertNotIn(self.customer_in_other_org.id, ids)
