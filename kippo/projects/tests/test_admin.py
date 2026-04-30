import datetime
from datetime import date
from http import HTTPStatus
from unittest import TestCase
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.urls import reverse
from django.utils import timezone

from projects.admin import (
    KippoMilestoneAdmin,
    KippoProjectAdmin,
    ProjectWeeklyEffortAdminInline,
    _next_upsell_project_name,
    _start_of_next_month,
)
from projects.models import KippoMilestone, KippoProject, KippoProjectStatus, ProjectColumnSet


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

    def test_add_form_prefills_from_get_params(self):
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url, {"category": "upsell-improvement", "parent_project": str(self.project1.id)})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertEqual(adminform.form.initial.get("category"), "upsell-improvement")
        self.assertEqual(adminform.form.initial.get("parent_project"), str(self.project1.id))
        # parent_project widget should be hidden on add form
        self.assertEqual(adminform.form.fields["parent_project"].widget.input_type, "hidden")

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
