import datetime
from datetime import date
from html.parser import HTMLParser
from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlencode, urlparse

if TYPE_CHECKING:
    from octocat.models import GithubRepository

    from projects.admin import GithubRepositoryProjectInlineForm

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase
from customers.models import KippoCustomer
from django import forms
from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME, AdminForm
from django.db import connection
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from projects.admin import (
    CONTRACT_REQUIRED_FOR_UNDER_CONTRACT_MSG,
    ActiveKippoProjectAdmin,
    KippoMilestoneAdmin,
    KippoProjectAdmin,
    KippoProjectAdminForm,
    KippoProjectBaseAdmin,
    KippoProjectContractInline,
    ProjectAssignmentRateInline,
    ProjectWeeklyEffortAdminInline,
    SalesKippoProjectAdmin,
    _next_continuation_project_name,
    _start_of_next_month,
)
from projects.definitions import DEFAULT_BILLING_TYPE, DEFAULT_PRICING_BASIS
from projects.filters import PhaseMultiSelectListFilter
from projects.models import (
    DEFAULT_ACTIVE_PROJECT_PHASES,
    PHASE_UNDER_CONTRACT,
    SALES_PROJECT_PHASES,
    VALID_PROJECT_PHASES,
    ActiveKippoProject,
    KippoMilestone,
    KippoProject,
    KippoProjectContract,
    KippoProjectOrganizationCategory,
    KippoProjectStatus,
    KippoProjectUserStatisfactionResult,
    ProjectColumnSet,
    ProjectWeeklyEffort,
    SalesKippoProject,
)


def _global_category(key: str) -> KippoProjectOrganizationCategory:
    """Fetch a seeded global (organization=null) project category by key, for KippoProject.category FK in tests."""
    return KippoProjectOrganizationCategory.objects.get(organization__isnull=True, key=key)


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
            category=_global_category("other"),
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
            category=_global_category("other"),
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

        inline = ProjectWeeklyEffortAdminInline(parent_model=KippoProject, admin_site=self.site)

        # non-superuser: scoped to themselves only (may only log their own effort)
        orguser_request = MockRequest()
        orguser_request.user = self.organization_usera
        formset = inline.get_formset(request=orguser_request, obj=self.project1)
        actual = set(u.id for u in formset.form.base_fields["user"].queryset)
        self.assertEqual(actual, {self.organization_usera.id})

        # superuser: any member of the project's organization
        formset = inline.get_formset(request=self.super_user_request, obj=self.project1)
        actual = set(u.id for u in formset.form.base_fields["user"].queryset)
        self.assertEqual(actual, set(self.organization_users))

    def _save_contract_inline(self, project: KippoProject, form_data: dict) -> KippoProjectContract:
        """Drive the contract inline formset the way the admin does, returning the saved contract."""
        inline = KippoProjectContractInline(parent_model=KippoProject, admin_site=self.site)
        formset_class = inline.get_formset(request=self.super_user_request, obj=project)
        prefix = "contract"
        data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1",
            # pricing_basis renders with its default selected; a browser posts it. Callers override via form_data.
            f"{prefix}-0-pricing_basis": "fixed",
        }
        data.update({f"{prefix}-0-{field}": value for field, value in form_data.items()})
        formset = formset_class(data=data, instance=project, prefix=prefix)
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        return project.contract

    def test_contract_inline_autopopulates_period_from_project(self):
        # the contract inline leaves start/end blank -> KippoProjectContract.save() fills them
        # from the project (start_date / target_date), so the admin user need not retype them.
        self.project1.start_date = date(2026, 2, 1)
        self.project1.target_date = date(2026, 8, 31)
        self.project1.save()

        contract = self._save_contract_inline(
            self.project1,
            {"billing_type": "monthly", "total_amount": "300000", "start_date": "", "end_date": "", "note": ""},
        )
        self.assertEqual(contract.start_date, date(2026, 2, 1))
        self.assertEqual(contract.end_date, date(2026, 8, 31))

    def test_contract_inline_preserves_explicit_period(self):
        # an explicitly entered period is not overwritten by the project defaults
        self.project1.start_date = date(2026, 2, 1)
        self.project1.target_date = date(2026, 8, 31)
        self.project1.save()

        contract = self._save_contract_inline(
            self.project1,
            {"billing_type": "monthly", "total_amount": "300000", "start_date": "2026-03-15", "end_date": "2026-05-15", "note": ""},
        )
        self.assertEqual(contract.start_date, date(2026, 3, 15))
        self.assertEqual(contract.end_date, date(2026, 5, 15))

    def test_contract_inline_extra_form_prefilled_with_project_period(self):
        # the blank "add contract" row shows the project's dates as initial values
        self.project1.start_date = date(2026, 2, 1)
        self.project1.target_date = date(2026, 8, 31)
        self.project1.save()

        inline = KippoProjectContractInline(parent_model=KippoProject, admin_site=self.site)
        formset_class = inline.get_formset(request=self.super_user_request, obj=self.project1)
        formset = formset_class(instance=self.project1)
        extra_form = formset.extra_forms[0]
        self.assertEqual(extra_form.initial.get("start_date"), date(2026, 2, 1))
        self.assertEqual(extra_form.initial.get("end_date"), date(2026, 8, 31))

    def test_contract_inline_untouched_prefilled_row_creates_no_contract(self):
        # submitting the pre-filled row without entering an amount must not create a contract
        self.project1.start_date = date(2026, 2, 1)
        self.project1.target_date = date(2026, 8, 31)
        self.project1.save()

        inline = KippoProjectContractInline(parent_model=KippoProject, admin_site=self.site)
        formset_class = inline.get_formset(request=self.super_user_request, obj=self.project1)
        prefix = "contract"
        data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1",
            # period echoes the pre-filled initial; billing_type / pricing_basis echo their defaults;
            # total_amount left blank
            f"{prefix}-0-billing_type": "delivery",
            f"{prefix}-0-pricing_basis": "fixed",
            f"{prefix}-0-total_amount": "",
            f"{prefix}-0-start_date": "2026-02-01",
            f"{prefix}-0-end_date": "2026-08-31",
            f"{prefix}-0-note": "",
        }
        formset = formset_class(data=data, instance=self.project1, prefix=prefix)
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.assertIsNone(getattr(self.project1, "contract", None))

    def _contract_gate_formset(self, project: KippoProject, form_data: dict | None) -> BaseInlineFormSet:
        """Build the contract inline formset (no period prefill) bound to ``project`` as its parent."""
        inline = KippoProjectContractInline(parent_model=KippoProject, admin_site=self.site)
        formset_class = inline.get_formset(request=self.super_user_request, obj=None)  # obj=None -> no period prefill
        prefix = "contract"
        data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1",
        }
        # a browser always posts the billing_type / pricing_basis selects at their rendered defaults even
        # when the operator enters nothing else; mirror that so an otherwise-empty row is treated as
        # unchanged (creates no contract) rather than raising required-field errors.
        row = {"billing_type": DEFAULT_BILLING_TYPE, "pricing_basis": DEFAULT_PRICING_BASIS}
        if form_data:
            row.update(form_data)
        data.update({f"{prefix}-0-{field}": value for field, value in row.items()})
        return formset_class(data=data, instance=project, prefix=prefix)

    def _entering_under_contract_project(self) -> KippoProject:
        project = KippoProject.objects.get(pk=self.project1.pk)  # snapshot persisted phase (proposing-low)
        project.phase = PHASE_UNDER_CONTRACT  # transition -> entering
        self.assertTrue(project._is_entering_under_contract())
        return project

    def test_under_contract_gate_flags_inline_not_phase_when_contract_missing(self):
        # entering 契約(稼働中) with an empty contract inline: the requirement is surfaced on the contract
        # component (formset non-form error, so Django highlights it), NOT on the phase field, and blocks the save.
        project = self._entering_under_contract_project()
        formset = self._contract_gate_formset(project, form_data=None)
        self.assertFalse(formset.is_valid())
        self.assertTrue(formset.non_form_errors())
        self.assertIn(str(CONTRACT_REQUIRED_FOR_UNDER_CONTRACT_MSG), formset.non_form_errors())

    def test_under_contract_gate_passes_when_contract_period_submitted_same_request(self):
        # supplying a complete contract period in the SAME request satisfies the gate (the fix: previously
        # the model-level check queried the DB and rejected the contract-and-phase-in-one-save flow).
        project = self._entering_under_contract_project()
        formset = self._contract_gate_formset(
            project,
            form_data={
                "billing_type": DEFAULT_BILLING_TYPE,
                "pricing_basis": DEFAULT_PRICING_BASIS,
                "total_amount": "500000",
                "start_date": "2026-01-01",
                "end_date": "2026-06-30",
            },
        )
        self.assertTrue(formset.is_valid(), formset.non_form_errors() or formset.errors)

    def test_under_contract_gate_ignores_non_entering_edit(self):
        # a normal edit that does not transition into 契約(稼働中) is not gated, even with an empty inline
        project = KippoProject.objects.get(pk=self.project1.pk)  # phase stays proposing-low
        self.assertFalse(project._is_entering_under_contract())
        formset = self._contract_gate_formset(project, form_data=None)
        self.assertTrue(formset.is_valid(), formset.non_form_errors())

    def test_under_contract_gate_passes_when_new_contract_period_backfills(self):
        # a NEW contract row left blank still satisfies the gate: KippoProjectContract.save() backfills the
        # period from the project's start_date / target_date on creation, so the saved contract is complete.
        project = KippoProject.objects.get(pk=self.project1.pk)
        project.start_date = date(2026, 1, 1)
        project.target_date = date(2026, 6, 30)
        project.save()
        entering = KippoProject.objects.get(pk=project.pk)
        entering.phase = PHASE_UNDER_CONTRACT
        formset = self._contract_gate_formset(entering, form_data={"total_amount": "500000", "start_date": "", "end_date": ""})
        self.assertTrue(formset.is_valid(), formset.non_form_errors() or formset.errors)

    def test_under_contract_gate_blocks_when_contract_deleted_same_request(self):
        # entering 契約(稼働中) while DELETING the only contract in the same save must be blocked — the gate
        # judges the post-save state, not the still-persisted DB row.
        project = KippoProject.objects.get(pk=self.project1.pk)
        project.start_date = date(2026, 1, 1)
        project.target_date = date(2026, 6, 30)
        project.save()
        contract = KippoProjectContract.objects.create(project=project, total_amount=100000)  # period backfills
        self.assertTrue(contract.has_complete_period())
        entering = KippoProject.objects.get(pk=project.pk)
        entering.phase = PHASE_UNDER_CONTRACT
        inline = KippoProjectContractInline(parent_model=KippoProject, admin_site=self.site)
        formset_class = inline.get_formset(request=self.super_user_request, obj=entering)
        prefix = "contract"
        data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "1",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1",
            f"{prefix}-0-id": str(contract.pk),
            f"{prefix}-0-billing_type": contract.billing_type,
            f"{prefix}-0-pricing_basis": contract.pricing_basis,
            f"{prefix}-0-total_amount": str(contract.total_amount),
            f"{prefix}-0-start_date": contract.start_date.isoformat(),
            f"{prefix}-0-end_date": contract.end_date.isoformat(),
            f"{prefix}-0-DELETE": "on",
        }
        formset = formset_class(data=data, instance=entering, prefix=prefix)
        self.assertFalse(formset.is_valid())
        self.assertIn(str(CONTRACT_REQUIRED_FOR_UNDER_CONTRACT_MSG), formset.non_form_errors())

    def test_show_github_project_html_url_displays_last_path_component(self):
        # the changelist GITHUBプロジェクト cell links to the full URL but shows only the trailing component
        admin_instance = KippoProjectAdmin(KippoProject, self.site)
        self.project1.github_project_html_url = "https://github.com/orgs/acme/projects/42"
        html = admin_instance.show_github_project_html_url(self.project1)
        self.assertIn('href="https://github.com/orgs/acme/projects/42"', html)
        self.assertIn(">42<", html)
        self.assertNotIn(">https://github.com", html)

    def test_show_github_project_html_url_blank_when_unset(self):
        admin_instance = KippoProjectAdmin(KippoProject, self.site)
        self.project1.github_project_html_url = ""
        self.assertEqual(admin_instance.show_github_project_html_url(self.project1), "")


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
            category=_global_category("other"),
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
            category=_global_category("other"),
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
            category=_global_category("other"),
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
            category=_global_category("other"),
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

    def test_contract_inline_renders_directly_after_dates_and_estimates(self):
        # The change_form template pulls the 契約 inline out of the default inline block and emits it
        # directly below the "Dates & Estimates" fieldset. Assert the rendered order:
        #   Dates & Estimates (allocated_staff_days) < 契約 (billing_type) < Details (document_folder_url).
        # Field names are used as markers because they are prefix-independent and absent from the <head>.
        KippoProjectContract.objects.create(
            project=self.project1,
            billing_type="monthly",
            pricing_basis="fixed",
            total_amount=300000,
            start_date=self.current_date,
            end_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        url = reverse("admin:projects_kippoproject_change", args=[self.project1.id])
        self.client.force_login(self.superuser_no_org)
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode()
        dates_idx = content.index("allocated_staff_days")
        contract_idx = content.index("billing_type")
        details_idx = content.index("document_folder_url")
        self.assertLess(dates_idx, contract_idx, "契約 inline should render after the Dates & Estimates fieldset")
        self.assertLess(contract_idx, details_idx, "契約 inline should render before the Details fieldset")
        # A contract exists, so the project's own start/target inputs are hidden (contract is the source of truth).
        self.assertNotIn('name="start_date"', content)
        self.assertNotIn('name="target_date"', content)


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
            category=_global_category("other"),
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.project2 = KippoProject.objects.create(
            organization=self.organization,
            name="project2",
            category=_global_category("other"),
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.changelist_url = reverse("admin:projects_kippoproject_changelist")
        self.client.force_login(self.superuser_no_org)

    def test_changelist_includes_single_line_column_style(self):
        # the changelist template widens the プロジェクト名(name) + 顧客(customer) + フェーズ(phase) columns to a single line
        response = self.client.get(self.changelist_url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode()
        self.assertIn("td.field-name", content)
        self.assertIn("td.field-get_customer_name", content)
        self.assertIn("td.field-phase", content)
        self.assertIn("white-space: nowrap", content)

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

    def test_no_continuation_requires_close_comment(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
                "post": "yes",
                "category": "__no_continuation__",
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

    def test_no_continuation_flow_closes_project(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
                "post": "yes",
                "category": "__no_continuation__",
                "close_comment": "Project completed successfully.",
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertIn("/admin/projects/kippoproject/", response["Location"])
        # ensure no add page is requested for continuation-none
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

    def test_continuation_flow_closes_and_redirects(self):
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
                "post": "yes",
                "category": "continuation",
                "close_comment": "",
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertIn("/admin/projects/kippoproject/add/", response["Location"])
        parsed = urlparse(response["Location"])
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("category"), [str(_global_category("continuation").pk)])
        self.assertEqual(params.get("parent_project"), [str(self.project1.id)])
        # close-action continuation redirect must include the parent's organization and the continuation marker
        # so the add form can derive the org server-side and detect the entry point.
        self.assertEqual(params.get("organization"), [str(self.project1.organization_id)])
        self.assertEqual(params.get("_continuation_source"), ["close"])

        self.project1.refresh_from_db()
        self.assertTrue(self.project1.is_closed)
        self.assertFalse(self.project1.display_as_active)
        self.assertFalse(self.project1.display_in_project_report)
        # category on the closing project is unchanged
        self.assertEqual(self.project1.category.key, "other")

    def test_continuation_redirect_prefills_new_project_fields(self):
        # populate source-project fields that should propagate to the continuation child
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
                "category": "continuation",
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

    def test_continuation_redirect_omits_blank_source_fields(self):
        # leave optional source fields blank: prefill params should not include empty values
        response = self.client.post(
            self.changelist_url,
            data={
                "action": "close_kippoproject_action",
                ACTION_CHECKBOX_NAME: [str(self.project1.id)],
                "post": "yes",
                "category": "continuation",
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

    def test_close_form_category_dropdown_default_is_no_continuation(self):
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
        self.assertEqual(form.fields["category"].initial, "__no_continuation__")

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
        # the close-wizard opens /add/?_continuation_source=close&... and prefills the new project's fields;
        # parent_project is present (hidden) on this wizard add (kippo#41).
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(
            url,
            {
                "_continuation_source": "close",
                "category": "continuation",
                "parent_project": str(self.project1.id),
                "name": "prefilled-name",
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertEqual(adminform.form.initial.get("name"), "prefilled-name")
        self.assertEqual(adminform.form.initial.get("parent_project"), str(self.project1.id))
        self.assertIn("parent_project", adminform.form.fields)

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
            name="continuation-child",
            category=_global_category("continuation"),
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

    def test_manual_add_form_omits_parent_project(self):
        # kippo#41: the flat /add/ form no longer exposes parent_project (continuation creation is wizard-only)
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertNotIn("parent_project", response.context["adminform"].form.fields)

    def test_plain_add_excludes_continuation_category(self):
        # kippo#41: the 継続 category is hidden on the plain add form (it needs a parent_project)
        from projects.definitions import CONTINUATION_CATEGORY_VALUE

        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        keys = set(response.context["adminform"].form.fields["category"].queryset.values_list("key", flat=True))
        self.assertNotIn(CONTINUATION_CATEGORY_VALUE, keys, "continuation category leaked onto plain add")

    def test_continuation_wizard_add_keeps_continuation_category(self):
        # the close-wizard add (?_continuation_source=close) keeps the 継続 category selectable
        from projects.definitions import CONTINUATION_CATEGORY_VALUE

        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url, {"_continuation_source": "close"})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        keys = set(response.context["adminform"].form.fields["category"].queryset.values_list("key", flat=True))
        self.assertIn(CONTINUATION_CATEGORY_VALUE, keys, "wizard add must keep the continuation category available")

    def test_add_form_category_scoped_to_user_organizations(self):
        # the category select must list only the user's organizations' categories, never another org's
        other_org_category = KippoProjectOrganizationCategory.objects.filter(organization=self.other_organization).first()
        self.assertIsNotNone(other_org_category, "fixture should have seeded the other org's categories")
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        queryset = response.context["adminform"].form.fields["category"].queryset
        org_ids = set(queryset.values_list("organization_id", flat=True))
        self.assertEqual(org_ids, {self.organization.id}, f"category select leaked non-member org categories: {org_ids}")
        self.assertNotIn(other_org_category.pk, set(queryset.values_list("pk", flat=True)))

    def test_add_form_preselects_org_default_category(self):
        # the model default is a GLOBAL row the org-scoped queryset drops; the add form must still
        # pre-select the org's OWN default category (and keep it selectable) so a plain registration
        # validates without opening the カテゴリ dropdown.
        default_category = KippoProjectOrganizationCategory.get_default_for_organization(self.organization.id)
        self.assertIsNotNone(default_category)
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        field = response.context["adminform"].form.fields["category"]
        self.assertEqual(str(field.initial), str(default_category.pk))
        self.assertIn(default_category.pk, set(field.queryset.values_list("pk", flat=True)))

    def test_change_form_category_scoped_to_project_organization(self):
        # editing a project must only offer that project's organization's categories
        other_org_category = KippoProjectOrganizationCategory.objects.filter(organization=self.other_organization).first()
        url = reverse("admin:projects_kippoproject_change", args=[self.project1.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        queryset = response.context["adminform"].form.fields["category"].queryset
        org_ids = set(queryset.values_list("organization_id", flat=True))
        self.assertEqual(org_ids, {self.organization.id}, f"category select leaked non-project org categories: {org_ids}")
        self.assertNotIn(other_org_category.pk, set(queryset.values_list("pk", flat=True)))

    def test_continuation_redirect_hides_parent_project_and_organization(self):
        # close-action continuation redirect: parent_project and organization must render as hidden inputs
        # so the user cannot edit them; the values still POST so the existing validator runs.
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(
            url,
            {
                "_continuation_source": "close",
                "category": "continuation",
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

    def test_continuation_form_with_derived_organization_is_valid_and_saves_parent(self):
        # the continuation prefill always sets organization = parent_project.organization, so the form
        # validator's parent-org invariant is satisfied by construction. Save and verify the result.
        customer = KippoCustomer.objects.create(
            organization=self.project1.organization,
            name="continuation-form-customer",
            created_by=self.superuser_no_org,
            updated_by=self.superuser_no_org,
        )
        form = KippoProjectAdminForm(
            data={
                "organization": str(self.project1.organization_id),
                "parent_project": str(self.project1.id),
                "name": "project1 Phase 2",
                "phase": "proposing-low",
                "confidence": "80",
                "category": str(_global_category("continuation").pk),
                "columnset": str(self.project1.columnset_id),
                # required at registration (kippo#40 / T19, extended kippo#41)
                "customer": str(customer.id),
                "project_manager": str(self.superuser_no_org.id),
                "start_date": self.current_date.isoformat(),
                "target_date": self.current_date.isoformat(),
                "allocated_staff_days": "10",
                "problem_definition": "continuation problem",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        new_project = form.save(commit=False)
        new_project.created_by = self.superuser_no_org
        new_project.updated_by = self.superuser_no_org
        new_project.save()
        self.assertEqual(new_project.parent_project_id, self.project1.id)
        self.assertEqual(new_project.organization_id, self.project1.organization_id)

    def test_continuation_form_with_mismatched_organization_is_rejected(self):
        # tampered POST: hidden organization differs from parent_project.organization — the existing
        # validator must catch the mismatch (no validator changes were made for this issue).
        form = KippoProjectAdminForm(
            data={
                # parent is in self.organization, but the submitted organization is the other org
                "organization": str(self.other_organization.id),
                "parent_project": str(self.project1.id),
                "name": "tampered-project",
                "phase": "proposing-low",
                "confidence": "80",
                "category": str(_global_category("continuation").pk),
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
        self.customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="adminform-customer",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.parent = KippoProject.objects.create(
            organization=self.organization,
            name="parent-project",
            category=_global_category("other"),
            columnset=self.columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def _form_data(self, *, category: str, parent_project_id: str | None = None) -> dict:
        # category is a FK ModelChoiceField — submit the category's PK, resolved from its key.
        # customer / project_manager / dates / allocated_staff_days / problem_definition are all
        # required at registration (kippo#40 / T19, extended in kippo#41).
        data = {
            "organization": str(self.organization.id),
            "name": "manual-new-project",
            "phase": "proposing-low",
            "confidence": "80",
            "category": str(_global_category(category).pk),
            "columnset": str(self.columnset.pk),
            "customer": str(self.customer.id),
            "project_manager": str(self.github_manager.id),
            "start_date": self.current_date.isoformat(),
            "target_date": self.current_date.isoformat(),
            "allocated_staff_days": "10",
            "problem_definition": "solve the thing",
        }
        if parent_project_id:
            data["parent_project"] = parent_project_id
        return data

    def test_continuation_category_without_parent_project_is_invalid(self):
        form = KippoProjectAdminForm(data=self._form_data(category="continuation"))
        self.assertFalse(form.is_valid())
        self.assertIn("parent_project", form.errors)

    def test_continuation_category_with_parent_project_is_valid(self):
        form = KippoProjectAdminForm(
            data=self._form_data(category="continuation", parent_project_id=str(self.parent.id)),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_non_continuation_category_does_not_require_parent_project(self):
        form = KippoProjectAdminForm(data=self._form_data(category="other"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_registration_requires_customer_and_start_date(self):
        # add-form missing the slim required registration fields is invalid (kippo#40 / T19, slimmed)
        data = self._form_data(category="other")
        for field in ("customer", "start_date"):
            del data[field]
        form = KippoProjectAdminForm(data=data)
        self.assertFalse(form.is_valid())
        for field in ("customer", "start_date"):
            self.assertIn(field, form.errors)

    def test_registration_does_not_require_pm_dates_or_problem_definition(self):
        # slimmed registration: PM / target_date / problem_definition are added on a later edit
        data = self._form_data(category="other")
        for field in ("project_manager", "target_date", "problem_definition"):
            del data[field]
        form = KippoProjectAdminForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_non_full_confidence_allows_zero_or_blank_allocated_staff_days(self):
        # phase proposing-low → confidence < 100 → allocated_staff_days need not be positive
        data = self._form_data(category="other")  # _form_data uses phase=proposing-low
        data["allocated_staff_days"] = "0"
        self.assertTrue(KippoProjectAdminForm(data=data).is_valid(), "0 allowed below full confidence")
        del data["allocated_staff_days"]
        self.assertTrue(KippoProjectAdminForm(data=data).is_valid(), "blank allowed below full confidence")

    def test_full_confidence_requires_positive_allocated_staff_days(self):
        # phase under-contract → confidence 100 → allocated_staff_days must be > 0
        for bad in ("0", None):
            data = self._form_data(category="other")
            data["phase"] = "under-contract"
            if bad is None:
                del data["allocated_staff_days"]
            else:
                data["allocated_staff_days"] = bad
            form = KippoProjectAdminForm(data=data)
            self.assertFalse(form.is_valid(), f"allocated_staff_days={bad} should be invalid at full confidence")
            self.assertIn("allocated_staff_days", form.errors)

    def test_full_confidence_with_positive_allocated_staff_days_is_valid(self):
        data = self._form_data(category="other")
        data["phase"] = "completed"  # confidence 100
        data["allocated_staff_days"] = "5"
        form = KippoProjectAdminForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_closed_project_exempt_from_full_confidence_allocated_requirement(self):
        # the positive-allocated rule does not apply to closed projects
        closed = KippoProject.objects.create(
            organization=self.organization,
            name="closed-full-confidence",
            category=_global_category("other"),
            columnset=self.columnset,
            phase="completed",
            is_closed=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        data = self._form_data(category="other")
        data["phase"] = "completed"
        data["allocated_staff_days"] = "0"
        form = KippoProjectAdminForm(instance=closed, data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_non_project_category_exempt_from_full_confidence_allocated_requirement(self):
        # non-project (internal/overhead) categories are exempt even at full confidence
        data = self._form_data(category="non-project")
        data["phase"] = "completed"  # confidence 100
        data["allocated_staff_days"] = "0"
        form = KippoProjectAdminForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_existing_project_not_blocked_by_new_required_fields(self):
        # the create-only rule also covers the kippo#41 additions — editing an existing project with a
        # blank allocated_staff_days / problem_definition must still validate
        existing = KippoProject.objects.create(
            organization=self.organization,
            name="pre-existing-no-estimate",
            category=_global_category("other"),
            columnset=self.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        data = self._form_data(category="other")
        for field in ("allocated_staff_days", "problem_definition"):
            del data[field]
        form = KippoProjectAdminForm(instance=existing, data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_full_confidence_project_not_blocked_by_blank_allocated_staff_days(self):
        # create-only rule (kippo#41): a full-confidence EXISTING project with a blank estimate must
        # still validate on /change/ — otherwise a project-status (KippoProjectStatus) comment can't
        # be added until the estimate is filled. The requirement stays enforced on /add/ (see
        # test_full_confidence_requires_positive_allocated_staff_days).
        existing = KippoProject.objects.create(
            organization=self.organization,
            name="existing-full-confidence-no-estimate",
            category=_global_category("other"),
            columnset=self.columnset,
            phase="completed",  # confidence 100, not closed
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.assertFalse(existing.is_closed)
        data = self._form_data(category="other")
        data["phase"] = "completed"
        del data["allocated_staff_days"]
        form = KippoProjectAdminForm(instance=existing, data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_existing_project_not_blocked_by_registration_requirements(self):
        # editing an existing customer-less / PM-less project must still validate (create-only rule)
        existing = KippoProject.objects.create(
            organization=self.organization,
            name="pre-existing-minimal",
            category=_global_category("other"),
            columnset=self.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        data = self._form_data(category="other")
        for field in ("customer", "project_manager", "start_date", "target_date"):
            del data[field]
        form = KippoProjectAdminForm(instance=existing, data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_contract_inline_not_required_and_hidden_on_add(self):
        # the contract is added on a later edit — nothing contract-related at registration
        inline = KippoProjectContractInline(parent_model=KippoProject, admin_site=self.site)
        self.assertFalse(inline.get_min_num(request=self.super_user_request, obj=None))
        self.assertFalse(inline.get_min_num(request=self.super_user_request, obj=self.parent))
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        self.assertNotIn(KippoProjectContractInline, modeladmin.get_inlines(self.super_user_request, obj=None))
        self.assertIn(KippoProjectContractInline, modeladmin.get_inlines(self.super_user_request, obj=self.parent))

    def test_change_form_with_continuation_category_uses_persisted_parent_when_field_omitted(self):
        # change form: parent_project is readonly so it isn't submitted in POST data;
        # validation must fall back to the persisted instance value to avoid a false-positive error.
        existing_continuation = KippoProject.objects.create(
            organization=self.organization,
            name="existing-continuation",
            category=_global_category("continuation"),
            columnset=self.columnset,
            parent_project=self.parent,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        form = KippoProjectAdminForm(
            instance=existing_continuation,
            data=self._form_data(category="continuation"),  # no parent_project key
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_cross_org_parent_project_submission_is_invalid(self):
        # parent_project from a different organization than the submitted org should be rejected
        cross_org_parent = KippoProject.objects.create(
            organization=self.other_organization,
            name="cross-org-parent",
            category=_global_category("other"),
            columnset=self.columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        form = KippoProjectAdminForm(
            data=self._form_data(category="continuation", parent_project_id=str(cross_org_parent.id)),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("parent_project", form.errors)


class ContinuationPrefillHelpersTestCase(TestCase):
    def test_next_continuation_project_name_appends_phase_2_when_no_existing_suffix(self):
        self.assertEqual(_next_continuation_project_name("Foo"), "Foo Phase 2")
        self.assertEqual(_next_continuation_project_name("Foo Bar"), "Foo Bar Phase 2")

    def test_next_continuation_project_name_increments_existing_phase_number(self):
        self.assertEqual(_next_continuation_project_name("Foo Phase 2"), "Foo Phase 3")
        self.assertEqual(_next_continuation_project_name("Foo Phase 9"), "Foo Phase 10")
        self.assertEqual(_next_continuation_project_name("Foo Bar Phase 11"), "Foo Bar Phase 12")

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
            category=_global_category("other"),
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


class KippoProjectAdminCustomerSearchTestCase(KippoProjectAdminFixtureTestCaseBase):
    """KippoProjectAdmin changelist search matches on the related customer's name."""

    def setUp(self):
        super().setUp()
        self.changelist_url = reverse("admin:projects_kippoproject_changelist")
        self.acme = KippoCustomer.objects.create(
            organization=self.organization,
            name="Acme Corporation",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.acme_project = self.make_project("alpha-delivery")
        self.acme_project.customer = self.acme
        self.acme_project.save()
        self.other_project = self.make_project("beta-delivery")  # no customer

    def test_search_by_customer_name_returns_only_matching_project(self):
        response = self.client.get(self.changelist_url, {"q": "Acme"})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        results = response.context["cl"].queryset
        self.assertIn(self.acme_project, results)
        self.assertNotIn(self.other_project, results)


class KippoProjectAdminReturnToTestCase(KippoProjectAdminFixtureTestCaseBase):
    """`_return_to` round-trip: a project add/change started from another admin page (e.g. the
    customer admin's プロジェクトを追加 button) redirects back there on save and prefills the customer.
    """

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.modeladmin = KippoProjectAdmin(KippoProject, self.site)
        self.customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="ReturnTo Co",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.return_to = reverse("admin:customers_kippocustomer_change", args=[self.customer.id])

    def _add_request(self, params: dict, post_data: dict) -> HttpRequest:
        # The add form posts to its own URL (empty form action), so the query string survives into
        # response_add — RequestFactory mirrors that by parsing the path's query string into GET.
        request = self.factory.post(f"/admin/projects/activekippoproject/add/?{urlencode(params)}", post_data)
        request.user = self.superuser_no_org
        return request

    def test_get_changeform_initial_data_prefills_customer(self):
        request = self.factory.get("/add/", {"customer": str(self.customer.id), "organization": str(self.organization.id)})
        request.user = self.superuser_no_org
        initial = self.modeladmin.get_changeform_initial_data(request)
        self.assertEqual(initial["customer"], str(self.customer.id))
        self.assertEqual(initial["organization"], str(self.organization.id))

    def test_safe_return_to_accepts_relative_admin_url(self):
        request = self._add_request({"_return_to": self.return_to}, {"_save": ""})
        self.assertEqual(self.modeladmin._safe_return_to(request), self.return_to)

    def test_safe_return_to_rejects_external_url(self):
        request = self._add_request({"_return_to": "https://evil.example.com/x"}, {"_save": ""})
        self.assertIsNone(self.modeladmin._safe_return_to(request))

    def test_safe_return_to_none_when_absent(self):
        request = self._add_request({}, {"_save": ""})
        self.assertIsNone(self.modeladmin._safe_return_to(request))

    def test_plain_save_redirects_back_to_return_to(self):
        request = self._add_request({"_return_to": self.return_to}, {"_save": ""})
        result = self.modeladmin._redirect_back_after_save(request, HttpResponse())
        self.assertIsInstance(result, HttpResponseRedirect)
        self.assertEqual(result["Location"], self.return_to)

    def test_save_and_add_another_carries_return_and_customer_forward(self):
        params = {"_return_to": self.return_to, "customer": str(self.customer.id), "organization": str(self.organization.id)}
        request = self._add_request(params, {"_addanother": ""})
        original = HttpResponseRedirect(reverse("admin:projects_activekippoproject_add"))
        result = self.modeladmin._redirect_back_after_save(request, original)
        query = parse_qs(urlparse(result["Location"]).query)
        self.assertEqual(query["_return_to"], [self.return_to])
        self.assertEqual(query["customer"], [str(self.customer.id)])
        self.assertEqual(query["organization"], [str(self.organization.id)])

    def test_save_and_continue_carries_return_forward(self):
        change_url = reverse("admin:projects_activekippoproject_change", args=[self.customer.id])  # any change-like URL
        request = self._add_request({"_return_to": self.return_to}, {"_continue": ""})
        result = self.modeladmin._redirect_back_after_save(request, HttpResponseRedirect(change_url))
        query = parse_qs(urlparse(result["Location"]).query)
        self.assertEqual(query["_return_to"], [self.return_to])

    def test_no_return_to_leaves_response_unchanged(self):
        request = self._add_request({}, {"_save": ""})
        original = HttpResponse()
        self.assertIs(self.modeladmin._redirect_back_after_save(request, original), original)


class KippoProjectAdminActiveParityTestCase(KippoProjectAdminFixtureTestCaseBase):
    """After the refactor both registered admins subclass KippoProjectBaseAdmin and look
    near-identical: presentation (columns, ordering, change-page delete lock) lives on the base.
    The differences are the queryset (active-only, via the proxy manager), the always-hidden
    closure fields, and the display_as_active column that only the all-projects admin keeps.
    """

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = self.superuser_no_org

    def test_both_admins_subclass_shared_base(self):
        self.assertTrue(issubclass(KippoProjectAdmin, KippoProjectBaseAdmin))
        self.assertTrue(issubclass(ActiveKippoProjectAdmin, KippoProjectBaseAdmin))

    def test_active_admin_does_not_override_presentation(self):
        # parity comes from inheritance — these are not redefined on the active admin
        for attr in ("list_display", "ordering", "has_delete_permission", "get_ordering", "get_queryset"):
            self.assertNotIn(attr, ActiveKippoProjectAdmin.__dict__, f"{attr} should be inherited from the base")
        self.assertEqual(ActiveKippoProjectAdmin.list_display, KippoProjectBaseAdmin.list_display)

    def test_display_as_active_is_only_all_projects_column(self):
        # display_as_active is the ONLY column the all-projects admin adds over the shared base;
        # every other column (including phase, category, and the contract columns) lives on the base.
        self.assertEqual(KippoProjectAdmin.list_display, (*KippoProjectBaseAdmin.list_display, "display_as_active"))
        self.assertNotIn("display_as_active", ActiveKippoProjectAdmin.list_display)
        for column in ("phase", "category", "get_contract_billing_type_display", "get_contract_total_amount_display"):
            self.assertIn(column, KippoProjectBaseAdmin.list_display)

    def test_confidence_column_retired_from_both_admins(self):
        # confidence is no longer a changelist column on either admin (kept as a model field for
        # ordering/forms, but the get_confidence_display column was retired)
        self.assertNotIn("get_confidence_display", KippoProjectBaseAdmin.list_display)
        self.assertNotIn("get_confidence_display", KippoProjectAdmin.list_display)

    def test_change_page_hides_delete_button_but_changelist_keeps_it(self):
        project = self.make_project("delete-lock")
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        change_request = self.factory.get(reverse("admin:projects_kippoproject_change", args=[project.id]))
        change_request.user = self.superuser_no_org
        self.assertFalse(modeladmin.has_delete_permission(change_request, project))
        list_request = self.factory.get(reverse("admin:projects_kippoproject_changelist"))
        list_request.user = self.superuser_no_org
        self.assertTrue(modeladmin.has_delete_permission(list_request))

    def test_changelist_renders_for_non_superuser_org_member(self):
        # The non-superuser get_queryset branch (org filter + .distinct()) combines with the Case()
        # expression in get_ordering(). The fixture base logs in a superuser, which skips that
        # branch entirely — so drive the staff path explicitly to cover the DISTINCT + Case render.
        self.make_project("non-su-visible")
        self.client.force_login(self.staffuser_with_org)
        for model_path in ("projects_kippoproject", "projects_activekippoproject"):
            with self.subTest(model=model_path):
                response = self.client.get(reverse(f"admin:{model_path}_changelist"))
                self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_changelist_orders_non_project_category_first(self):
        # Regression guard: non-project-first ordering was previously dead (the order_by in
        # get_queryset was overridden by the `ordering` attribute). Names are chosen so the
        # non-project sorts LATER by name — proving the category ordering dominates the name tiebreak.
        real = self.make_project("aaa-real-delivery")  # category "other"
        anon = KippoProject.objects.create(
            organization=self.organization,
            name="zzz-anon-bucket",
            category=_global_category("non-project"),
            columnset=self.columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        response = self.client.get(reverse("admin:projects_kippoproject_changelist"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        names = [p.name for p in response.context["cl"].result_list]
        self.assertLess(names.index(anon.name), names.index(real.name))


class SalesKippoProjectAdminTestCase(KippoProjectAdminFixtureTestCaseBase):
    """プロジェクト(営業中): the pre-contract sales pipeline admin (SalesKippoProject proxy)."""

    def make_phase_project(self, name: str, phase: str, *, is_closed: bool = False) -> KippoProject:
        project = self.make_project(name)
        project.phase = phase
        project.is_closed = is_closed
        project.save()
        return project

    def test_verbose_name(self):
        self.assertEqual(str(SalesKippoProject._meta.verbose_name), "プロジェクト(営業中)")

    def test_list_display_drops_survey_and_github_columns(self):
        # get_list_display filters the shared base columns down to the sales set (three post-delivery
        # columns removed). The active admin renders the base columns unchanged, so compare against it.
        # The base binds get_projectstatus_display to a fresh per-request callable — normalize it back.
        request = RequestFactory().get("/")
        request.user = self.superuser_no_org

        def names(columns: tuple) -> tuple:
            return tuple("get_projectstatus_display" if callable(column) else column for column in columns)

        sales_columns = names(SalesKippoProjectAdmin(SalesKippoProject, self.site).get_list_display(request))
        active_columns = names(ActiveKippoProjectAdmin(ActiveKippoProject, self.site).get_list_display(request))
        dropped = ("get_kippoprojectuserstatisfactionresult_usernames", "get_projectsurvey_display_url", "show_github_project_html_url")
        self.assertEqual(sales_columns, tuple(column for column in active_columns if column not in dropped))
        for column in dropped:
            self.assertIn(column, active_columns)
            self.assertNotIn(column, sales_columns)

    def test_manager_filters_to_open_proposing_phases(self):
        in_pipeline = [self.make_phase_project(f"sales-{phase}", phase) for phase in SALES_PROJECT_PHASES]
        under_contract = self.make_phase_project("delivery", PHASE_UNDER_CONTRACT)
        completed = self.make_phase_project("done", "completed")
        closed_proposal = self.make_phase_project("closed-proposal", "proposing-high", is_closed=True)

        visible = set(SalesKippoProject.objects.values_list("id", flat=True))
        self.assertEqual(visible, {project.id for project in in_pipeline})
        for excluded in (under_contract, completed, closed_proposal):
            self.assertNotIn(excluded.id, visible)

    def test_changelist_renders(self):
        self.make_phase_project("sales-visible", "proposing-mid")
        response = self.client.get(reverse("admin:projects_saleskippoproject_changelist"))
        self.assertEqual(response.status_code, HTTPStatus.OK)


class KippoProjectAdminContractColumnsTestCase(KippoProjectAdminFixtureTestCaseBase):
    """The all-projects admin surfaces the related contract's 請求方法 / 契約金額 as changelist columns."""

    def setUp(self):
        super().setUp()
        self.modeladmin = KippoProjectAdmin(KippoProject, self.site)

    def test_columns_blank_when_no_contract(self):
        project = self.make_project("no-contract")
        self.assertEqual(self.modeladmin.get_contract_billing_type_display(project), "")
        self.assertEqual(self.modeladmin.get_contract_total_amount_display(project), "")

    def test_columns_render_contract_values(self):
        from projects.definitions import BILLING_TYPE_MONTHLY

        project = self.make_project("with-contract")
        KippoProjectContract.objects.create(
            project=project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=1500000,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        project.refresh_from_db()
        # billing_type shows the human choice label (月額), not the raw 'monthly'
        self.assertEqual(self.modeladmin.get_contract_billing_type_display(project), "月額")
        self.assertEqual(self.modeladmin.get_contract_total_amount_display(project), "¥1,500,000")

    def test_total_amount_blank_when_unset(self):
        # effort contracts may leave total_amount blank -> column renders empty, not "¥None"
        project = self.make_project("effort-no-total")
        KippoProjectContract.objects.create(
            project=project,
            total_amount=None,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        project.refresh_from_db()
        self.assertEqual(self.modeladmin.get_contract_total_amount_display(project), "")


class KippoProjectAdminCustomerAutocompleteTestCase(SimpleTestCase):
    """顧客 (customer) is selected via a searchable autocomplete on the project admins."""

    def test_project_admins_declare_customer_autocomplete(self):
        self.assertIn("customer", KippoProjectAdmin.autocomplete_fields)
        # ActiveKippoProjectAdmin inherits the base configuration.
        self.assertIn("customer", ActiveKippoProjectAdmin.autocomplete_fields)

    def test_autocomplete_config_passes_admin_system_checks(self):
        # admin.E039/E040 are raised here if KippoCustomer is not registered or its admin lacks
        # search_fields — i.e. the prerequisites that make the customer autocomplete actually work.
        errors = KippoProjectAdmin(KippoProject, admin.site).check()
        autocomplete_errors = [e for e in errors if e.id in {"admin.E038", "admin.E039", "admin.E040"}]
        self.assertEqual(autocomplete_errors, [])


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

    def test_add_view_hides_assignment_rate_and_monthly_assignment_inlines(self):
        from projects.admin import GithubRepositoryProjectInline, ProjectMonthlyAssignmentInline

        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        inlines = modeladmin.get_inlines(self.staff_user_request, obj=None)
        # Assignment rates use fixture defaults on /add/ (seeded in save_model); monthly assignments
        # and GitHub repositories are only meaningful once the project exists — all hidden on /add/.
        self.assertNotIn(ProjectAssignmentRateInline, inlines)
        self.assertNotIn(ProjectMonthlyAssignmentInline, inlines)
        self.assertNotIn(GithubRepositoryProjectInline, inlines)

    def test_change_view_shows_assignment_rate_and_monthly_assignment_inlines(self):
        from projects.admin import ProjectMonthlyAssignmentInline

        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        inlines = modeladmin.get_inlines(self.staff_user_request, obj=self.project)
        self.assertIn(ProjectAssignmentRateInline, inlines)
        self.assertIn(ProjectMonthlyAssignmentInline, inlines)


class DefaultAssignmentRateLoaderTestCase(SimpleTestCase):
    """_default_assignment_rate_initial: skip unknown roles, fall back to the setting for a missing rate."""

    def test_skips_unknown_roles_and_defaults_missing_rate(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from django.conf import settings

        from projects import admin as projects_admin

        rows = [
            {"role": "developer", "rate_per_day": 123456},
            {"role": "bogus-role", "rate_per_day": 999},  # unknown role -> skipped
            {"role": "tester"},  # missing rate -> falls back to settings.DEFAULT_PROJECT_DAILY_RATE
        ]
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = Path(tmp) / "rates.json"
            fixture_path.write_text(json.dumps(rows), encoding="utf-8")
            with patch.object(projects_admin, "DEFAULT_ASSIGNMENT_RATES_FIXTURE", fixture_path):
                result = projects_admin._default_assignment_rate_initial()
        self.assertEqual(
            result,
            (
                {"role": "developer", "rate_per_day": 123456},
                {"role": "tester", "rate_per_day": settings.DEFAULT_PROJECT_DAILY_RATE},
            ),
        )

    def test_missing_fixture_returns_empty(self):
        from pathlib import Path
        from unittest.mock import patch

        from projects import admin as projects_admin

        with (
            patch.object(projects_admin, "DEFAULT_ASSIGNMENT_RATES_FIXTURE", Path("/nonexistent/does-not-exist.json")),
            self.assertLogs("projects.admin", level="ERROR"),
        ):
            self.assertEqual(projects_admin._default_assignment_rate_initial(), ())


class ProjectAssignmentRateInlineConfigTestCase(TestCase):
    def test_max_num_equals_role_count(self):
        from projects.definitions import ProjectRoles

        # one rate per role — no entries can be added beyond the number of defined roles
        self.assertEqual(ProjectAssignmentRateInline.max_num, len(ProjectRoles.choices()))

    def test_inline_expanded(self):
        # the assignment-rates section is shown expanded (not collapsed) so prefilled defaults are visible
        self.assertNotIn("collapse", getattr(ProjectAssignmentRateInline, "classes", ()) or ())


class KippoProjectAddFormLayoutTestCase(KippoProjectAdminFixtureTestCaseBase):
    """Project add-form layout changes: phase top section, confidence/closure hidden, sections expanded."""

    def setUp(self):
        super().setUp()
        self.existing_project = self.make_project("layout-existing-project")

    @staticmethod
    def _all_fieldset_fields(fieldsets: list) -> list:
        return [field for _label, opts in fieldsets for field in opts.get("fields", ())]

    def test_phase_in_top_section_on_add(self):
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        fieldsets = modeladmin.get_fieldsets(self.super_user_request, obj=None)
        top_fields = fieldsets[0][1]["fields"]
        self.assertIn("phase", top_fields)

    def test_add_fields_cover_all_registration_required_fields(self):
        # poka-yoke: every create-only required field must be on the flat add form (ADD_FIELDS), else
        # the form would reject a field the user can't see/fill (kippo#41)
        missing = set(KippoProjectAdminForm.REQUIRED_AT_REGISTRATION) - set(KippoProjectBaseAdmin.ADD_FIELDS)
        self.assertEqual(missing, set(), f"required-at-registration fields missing from ADD_FIELDS: {missing}")

    def test_confidence_not_in_form(self):
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        fieldsets = modeladmin.get_fieldsets(self.super_user_request, obj=None)
        self.assertNotIn("confidence", self._all_fieldset_fields(fieldsets))
        self.assertNotIn("confidence", modeladmin.readonly_fields)

    def test_closure_and_survey_section_not_displayed(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        for obj in (None, self.existing_project):
            fields = self._all_fieldset_fields(modeladmin.get_fieldsets(self.super_user_request, obj=obj))
            self.assertNotIn("close_comment", fields)
            self.assertNotIn("survey_issued", fields)

    def test_dates_and_estimates_section_expanded(self):
        for _label, opts in KippoProjectBaseAdmin.fieldsets:
            if "start_date" in opts.get("fields", ()):
                self.assertNotIn("collapse", opts.get("classes", ()))
                break
        else:
            self.fail("No 'Dates & Estimates' fieldset found")

    def test_details_section_expanded_on_add(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        fieldsets = modeladmin.get_fieldsets(self.super_user_request, obj=None)
        # Details (category) starts expanded on /add/.
        for _label, opts in fieldsets:
            if "category" in opts.get("fields", ()):
                self.assertNotIn("collapse", opts.get("classes", ()))

    def test_details_section_collapsed_on_change(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        fieldsets = modeladmin.get_fieldsets(self.super_user_request, obj=self.existing_project)
        collapsed = [opts for _label, opts in fieldsets if "collapse" in opts.get("classes", ())]
        # Details (document_folder_url + merged Extra fields) stays collapsed on change (kippo#41)
        self.assertTrue(any("document_folder_url" in opts.get("fields", ()) for opts in collapsed), "Details should stay collapsed on change")

    def test_columnset_not_in_add_or_change_fieldsets(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        for obj in (None, self.existing_project):
            self.assertNotIn("columnset", self._all_fieldset_fields(modeladmin.get_fieldsets(self.super_user_request, obj=obj)))

    def test_contract_inline_expanded_single_entry(self):
        self.assertNotIn("collapse", getattr(KippoProjectContractInline, "classes", ()) or ())
        self.assertEqual(KippoProjectContractInline.extra, 1)

    def test_billing_entry_inline_removed(self):
        from projects.admin import KippoProjectBillingEntryInline

        self.assertNotIn(KippoProjectBillingEntryInline, KippoProjectBaseAdmin.inlines)

    def test_billing_entry_inline_received_datetime_readonly(self):
        # received_datetime is auto-managed in save() — must be read-only in the inline so a typed
        # value can't be silently discarded when is_received is left unchecked.
        from projects.admin import KippoProjectBillingEntryInline

        self.assertIn("received_datetime", KippoProjectBillingEntryInline.readonly_fields)
        self.assertIn("received_datetime", KippoProjectBillingEntryInline.fields)


class KippoProjectAddFormBehaviorTestCase(KippoProjectAdminFixtureTestCaseBase):
    """Add-form runtime behavior: default assignment rates prefilled, customer hidden when prefilled."""

    def setUp(self):
        super().setUp()
        self.customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="layout-customer",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_save_model_seeds_default_assignment_rates_on_create(self):
        # the assignment-rate inline is hidden on /add/; save_model seeds the fixture defaults instead
        from projects.models import ProjectAssignmentRate

        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        project = KippoProject(
            organization=self.organization,
            name="seed-rates-project",
            category=_global_category("other"),
            columnset=self.columnset,
            start_date=self.current_date,
        )
        modeladmin.save_model(self.super_user_request, project, form=None, change=False)
        roles = set(ProjectAssignmentRate.objects.filter(project=project).values_list("role", flat=True))
        self.assertEqual(roles, {"developer", "project_manager", "tester"})

    def test_save_model_does_not_reseed_assignment_rates_on_change(self):
        # editing an existing project must not re-create / duplicate its rates
        from projects.models import ProjectAssignmentRate

        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        project = self.make_project("no-reseed-project")
        ProjectAssignmentRate.objects.create(
            project=project,
            role="developer",
            rate_per_day=1,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        modeladmin.save_model(self.super_user_request, project, form=None, change=True)
        self.assertEqual(ProjectAssignmentRate.objects.filter(project=project).count(), 1)

    def test_assignment_rate_inline_no_prefill_on_change(self):
        inline = ProjectAssignmentRateInline(parent_model=KippoProject, admin_site=self.site)
        formset_class = inline.get_formset(request=self.super_user_request, obj=self.make_project("rate-change-project"))
        self.assertEqual(formset_class.extra, 0)

    def test_customer_field_hidden_when_prefilled_from_customer_admin(self):
        url = reverse("admin:projects_activekippoproject_add")
        response = self.client.get(url, {"customer": str(self.customer.id), "organization": str(self.organization.id)})
        self.assertEqual(response.status_code, HTTPStatus.OK)
        form = response.context["adminform"].form
        self.assertIsInstance(form.fields["customer"].widget, forms.HiddenInput)

    def test_customer_field_visible_on_plain_add(self):
        url = reverse("admin:projects_activekippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        form = response.context["adminform"].form
        self.assertNotIsInstance(form.fields["customer"].widget, forms.HiddenInput)


class KippoProjectAdminContractPeriodFieldsTestCase(KippoProjectAdminFixtureTestCaseBase):
    """start_date/target_date disappear from the project change form once a contract exists —
    the contract period (synced onto the project) is the single editable input.
    """

    def setUp(self):
        super().setUp()
        self.existing_project = self.make_project("contract-period-project")

    @staticmethod
    def _all_fieldset_fields(fieldsets: list) -> list:
        return [f for _label, opts in fieldsets for f in opts.get("fields", ())]

    def test_dates_visible_without_contract(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        fields = self._all_fieldset_fields(modeladmin.get_fieldsets(self.super_user_request, obj=self.existing_project))
        self.assertIn("start_date", fields)
        self.assertIn("target_date", fields)

    def test_dates_hidden_with_contract(self):
        KippoProjectContract.objects.create(
            project=self.existing_project,
            total_amount=100000,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        project = KippoProject.objects.get(pk=self.existing_project.pk)
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        fields = self._all_fieldset_fields(modeladmin.get_fieldsets(self.super_user_request, obj=project))
        self.assertNotIn("start_date", fields)
        self.assertNotIn("target_date", fields)

    def test_add_form_keeps_start_date(self):
        # slim registration still collects the initial start_date; target_date arrives with the
        # contract (or a later edit)
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        fields = self._all_fieldset_fields(modeladmin.get_fieldsets(self.super_user_request, obj=None))
        self.assertIn("start_date", fields)
        self.assertNotIn("target_date", fields)

    def test_change_view_renders_with_contract(self):
        KippoProjectContract.objects.create(
            project=self.existing_project,
            total_amount=100000,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        url = reverse("admin:projects_kippoproject_change", args=[self.existing_project.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        form = response.context["adminform"].form
        self.assertNotIn("start_date", form.fields)
        self.assertNotIn("target_date", form.fields)


class ActiveKippoProjectAdminParentProjectFieldTestCase(KippoProjectAdminFixtureTestCaseBase):
    """parent_project (kippo#41): absent from the flat /add/ form; exposed (hidden) only on the continuation
    close-wizard add; in the Details section (readonly) on change.
    """

    def setUp(self):
        super().setUp()
        self.existing_project = self.make_project("existing-project")

    @staticmethod
    def _all_fieldset_fields(fieldsets: list) -> list:
        return [f for _label, opts in fieldsets for f in opts.get("fields", ())]

    def test_plain_add_fieldsets_are_flat_required_only(self):
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        fieldsets = modeladmin.get_fieldsets(self.super_user_request, obj=None)
        # a single, unlabeled section holding exactly the required ADD_FIELDS, in order
        self.assertEqual(len(fieldsets), 1)
        label, opts = fieldsets[0]
        self.assertIsNone(label)
        self.assertEqual(tuple(opts["fields"]), KippoProjectBaseAdmin.ADD_FIELDS)
        self.assertNotIn("parent_project", opts["fields"])

    def test_change_fieldsets_have_three_sections_with_parent_in_details(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        fieldsets = modeladmin.get_fieldsets(self.super_user_request, obj=self.existing_project)
        labels = [str(label) if label is not None else None for label, _opts in fieldsets]
        self.assertIn(None, labels)
        for section in ("Dates & Estimates", "Details"):
            self.assertIn(section, labels)
        self.assertNotIn("Extra", labels)  # Extra was merged into Details
        # category sits in the top section; parent_project + former-Extra fields in Details (readonly on change)
        self.assertIn("category", fieldsets[0][1]["fields"])
        details = next(opts["fields"] for label, opts in fieldsets if str(label) == "Details")
        self.assertIn("parent_project", details)
        self.assertIn("slack_channel_name", details)  # merged in from the removed Extra section

    def test_active_admin_change_omits_parent_project(self):
        # ActiveKippoProjectAdmin excludes parent_project on change (active projects aren't continuation-edited)
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        fieldsets = modeladmin.get_fieldsets(self.super_user_request, obj=self.existing_project)
        self.assertNotIn("parent_project", self._all_fieldset_fields(fieldsets))

    def test_class_fieldsets_attribute_unchanged_after_get_fieldsets(self):
        # get_fieldsets must not mutate the (inherited) class attribute — a fresh list is built each call.
        from copy import deepcopy

        before = deepcopy(ActiveKippoProjectAdmin.fieldsets)
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        modeladmin.get_fieldsets(self.super_user_request, obj=None)
        modeladmin.get_fieldsets(self.super_user_request, obj=self.existing_project)
        self.assertEqual(ActiveKippoProjectAdmin.fieldsets, before)

    def test_plain_add_view_omits_parent_project(self):
        # normal /add/ is flat + required-only — parent_project is not part of the form at all
        url = reverse("admin:projects_activekippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertNotIn("parent_project", response.context["adminform"].form.fields)

    def test_continuation_wizard_add_view_exposes_parent_project_hidden(self):
        # the close-wizard add (?_continuation_source=close) keeps the full sectioned form so parent_project
        # renders (hidden) and POSTs — see KippoProjectBaseAdmin.get_fieldsets.
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(
            url,
            {"_continuation_source": "close", "category": "continuation", "parent_project": str(self.existing_project.id)},
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        adminform = response.context["adminform"]
        self.assertIn("parent_project", adminform.form.fields)
        widget = adminform.form.fields["parent_project"].widget
        inner_widget = getattr(widget, "widget", widget)
        self.assertIsInstance(inner_widget, forms.HiddenInput)

    def test_continuation_wizard_add_post_preserves_parent_project_field(self):
        # form action="" posts to the same URL incl. its query string, so _continuation_source survives the
        # POST and get_fieldsets rebuilds the full form — an invalid submit still re-renders parent_project.
        url = reverse("admin:projects_kippoproject_add") + "?_continuation_source=close"
        response = self.client.post(url, {"name": "incomplete-continuation"})  # intentionally invalid
        self.assertEqual(response.status_code, HTTPStatus.OK)  # re-rendered with errors, not a redirect
        self.assertIn("parent_project", response.context["adminform"].form.fields)


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
    """columnset is never selectable in the admin: it is hidden from every form (add & change,
    superuser & non-superuser) and auto-assigned to the organization's default on create.
    """

    def test_add_view_omits_columnset_field_for_superuser(self):
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertNotIn("columnset", response.context["adminform"].form.fields)

    def test_add_view_omits_columnset_field_for_non_superuser(self):
        self.client.force_login(self.staffuser_with_org)
        url = reverse("admin:projects_kippoproject_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertNotIn("columnset", response.context["adminform"].form.fields)

    def test_change_view_omits_columnset_field(self):
        existing = self.make_project("columnset-change-target")
        url = reverse("admin:projects_kippoproject_change", args=[existing.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertNotIn("columnset", response.context["adminform"].form.fields)

    def test_save_model_auto_assigns_organization_default_columnset_on_create(self):
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        obj = KippoProject(organization=self.organization, name="auto-columnset-project", start_date=self.current_date)
        self.assertIsNone(obj.columnset_id)
        modeladmin.save_model(self.super_user_request, obj, form=None, change=False)
        obj.refresh_from_db()
        self.assertEqual(obj.columnset, self.organization.get_default_columnset())

    def test_save_model_preserves_existing_columnset_on_change(self):
        existing = self.make_project("columnset-preserve-target")
        modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)
        modeladmin.save_model(self.super_user_request, existing, form=None, change=True)
        existing.refresh_from_db()
        self.assertEqual(existing.columnset, self.columnset)


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


class StatusCommentOnChangeWithBlankEstimateTestCase(KippoProjectAdminFixtureTestCaseBase):
    """A full-confidence project with a blank allocated_staff_days must still accept a new
    KippoProjectStatus comment on /change/. The estimate requirement is create-only (kippo#41), so
    adding a status comment on an existing project is not blocked by a missing estimate.
    """

    def test_change_view_post_adds_status_comment_when_estimate_blank(self):
        from projects.admin import KippoProjectStatusAdminInline

        project = KippoProject.objects.create(
            organization=self.organization,
            name="full-confidence-blank-estimate",
            category=_global_category("other"),
            columnset=self.columnset,
            start_date=self.current_date,
            phase="completed",  # confidence 100, not closed
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.assertFalse(project.is_closed)
        self.assertIsNone(project.allocated_staff_days)

        url = reverse("admin:projects_activekippoproject_change", args=[project.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

        # Two inlines share the KippoProjectStatus model, so the editable one's prefix is
        # disambiguated at runtime — resolve it from the rendered formsets rather than hardcoding.
        status_prefix = next(
            ifs.formset.prefix for ifs in response.context["inline_admin_formsets"] if isinstance(ifs.opts, KippoProjectStatusAdminInline)
        )
        comment = "status update while estimate is blank"
        form_data = _extract_admin_form_post_data(response, project)
        form_data.update(
            {
                f"{status_prefix}-TOTAL_FORMS": "1",
                f"{status_prefix}-INITIAL_FORMS": "0",
                f"{status_prefix}-0-comment": comment,
                f"{status_prefix}-0-id": "",
                f"{status_prefix}-0-project": str(project.id),
            }
        )
        post_response = self.client.post(url, data=form_data, follow=False)
        self.assertEqual(post_response.status_code, HTTPStatus.FOUND, post_response.content[:1000])
        self.assertTrue(KippoProjectStatus.objects.filter(project=project, comment=comment).exists())


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


class ContractAdminBillingEntryReceivedByTestCase(KippoProjectAdminFixtureTestCaseBase):
    """KippoProjectContractAdmin.save_formset stamps received_by with the acting admin when a
    billing entry is marked received.
    """

    def setUp(self):
        super().setUp()
        self.project = self.make_project("received-by-project")
        # effort/no-effort -> empty ledger on creation, so the entry added via the formset below is
        # the only one (a fixed contract would auto-generate one at 2026-09-30 and collide)
        self.contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type="delivery",
            pricing_basis="effort",
            total_amount=None,
            end_date=date(2026, 9, 30),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_save_formset_stamps_received_by_with_acting_user(self):
        from projects.admin import KippoProjectBillingEntryInline, KippoProjectContractAdmin

        admin_obj = KippoProjectContractAdmin(KippoProjectContract, self.site)
        inline = KippoProjectBillingEntryInline(parent_model=KippoProjectContract, admin_site=self.site)
        formset_class = inline.get_formset(request=self.super_user_request, obj=self.contract)
        prefix = "billing_entries"
        data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
            f"{prefix}-0-billing_date": "2026-09-30",
            f"{prefix}-0-amount": "1000000",
            f"{prefix}-0-is_received": "on",
            f"{prefix}-0-note": "",
        }
        formset = formset_class(data=data, instance=self.contract, prefix=prefix)
        self.assertTrue(formset.is_valid(), formset.errors)
        admin_obj.save_formset(self.super_user_request, form=None, formset=formset, change=False)

        entry = self.contract.billing_entries.get()
        self.assertTrue(entry.is_received)
        self.assertEqual(entry.received_by, self.superuser_no_org)  # acting admin stamped as verifier
        self.assertIsNotNone(entry.received_datetime)


class KippoProjectChangelistQueryCountTestCase(IsStaffModelAdminTestCaseBase):
    """Guards the M4 changelist batching: the query count must not scale with row count.

    Before batching, each row triggered an effort Sum, a latest-status `.latest()`, a
    satisfaction-usernames query, and a per-org PublicHoliday query. After batching these
    are folded into the list query (subqueries + prefetch + request-scoped holiday cache),
    so rendering 3 vs 9 projects must issue the same number of queries.
    """

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        self.columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()
        self.changelist_url = reverse("admin:projects_kippoproject_changelist")
        self.client.force_login(self.superuser_no_org)

    def _create_project_with_related(self, name: str) -> KippoProject:
        project = KippoProject.objects.create(
            organization=self.organization,
            name=name,
            category=_global_category("other"),
            columnset=self.columnset,
            start_date=self.current_date,
            target_date=self.current_date + datetime.timedelta(days=90),
            allocated_staff_days=30,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        KippoProjectStatus.objects.create(
            project=project, comment=f"status for {name}", created_by=self.github_manager, updated_by=self.github_manager
        )
        ProjectWeeklyEffort.objects.create(
            project=project,
            user=self.github_manager,
            week_start=self.current_date,
            hours=8,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        KippoProjectUserStatisfactionResult.objects.create(
            project=project, fullfillment_score=3, growth_score=4, created_by=self.github_manager, updated_by=self.github_manager
        )
        return project

    def test_changelist_query_count_is_bounded(self):
        for i in range(3):
            self._create_project_with_related(f"qc-project-{i}")
        with CaptureQueriesContext(connection) as few_ctx:
            response = self.client.get(self.changelist_url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        few_query_count = len(few_ctx)

        for i in range(3, 9):
            self._create_project_with_related(f"qc-project-{i}")
        with CaptureQueriesContext(connection) as many_ctx:
            response = self.client.get(self.changelist_url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        many_query_count = len(many_ctx)

        self.assertEqual(
            few_query_count,
            many_query_count,
            f"changelist query count scales with rows (3 rows: {few_query_count}, 9 rows: {many_query_count}) — N+1 regression",
        )


class ActiveProjectPhaseFilterTestCase(IsStaffModelAdminTestCaseBase):
    """PhaseMultiSelectListFilter on the ActiveKippoProject changelist (multi-select, default-selected phases)."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.all_phases = ("verbal-order", "under-contract", "proposing-low", "completed")
        # one active project per phase (display_as_active=True / is_closed=False by default => all active)
        for phase in self.all_phases:
            KippoProject.objects.create(
                organization=self.organization,
                name=f"project-{phase}",
                phase=phase,
                category=_global_category("other"),
                columnset=columnset,
                start_date=timezone.now().date(),
                created_by=self.github_manager,
                updated_by=self.github_manager,
            )
        self.modeladmin = ActiveKippoProjectAdmin(ActiveKippoProject, self.site)

    def _changelist_phases(self, query: str = "") -> set:
        request = RequestFactory().get(f"/admin/projects/activekippoproject/{query}")
        request.user = self.superuser_no_org
        changelist = self.modeladmin.get_changelist_instance(request)
        return set(changelist.queryset.values_list("phase", flat=True))

    def _phase_filter_choices(self, query: str = "") -> list:
        request = RequestFactory().get(f"/admin/projects/activekippoproject/{query}")
        request.user = self.superuser_no_org
        changelist = self.modeladmin.get_changelist_instance(request)
        spec = next(f for f in changelist.filter_specs if isinstance(f, PhaseMultiSelectListFilter))
        return list(spec.choices(changelist))

    def test_filter_registered_on_active_admin_only(self):
        self.assertIn(PhaseMultiSelectListFilter, self.modeladmin.list_filter)
        # the all-projects admin keeps the default (empty) list_filter — the filter is active-only
        self.assertNotIn(PhaseMultiSelectListFilter, KippoProjectAdmin(KippoProject, self.site).list_filter)

    def test_default_phases_preselected_when_no_param(self):
        # no `phase` query param => only the two in-flight phases show
        self.assertEqual(self._changelist_phases(), set(DEFAULT_ACTIVE_PROJECT_PHASES))

    def test_single_phase_param_filters(self):
        self.assertEqual(self._changelist_phases("?phase=completed"), {"completed"})

    def test_multiple_phases_comma_separated(self):
        self.assertEqual(self._changelist_phases("?phase=proposing-low,completed"), {"proposing-low", "completed"})

    def test_empty_phase_param_shows_all_active(self):
        # an explicit empty param (全て / all deselected) overrides the defaults and filters nothing
        self.assertEqual(self._changelist_phases("?phase="), set(self.all_phases))

    def test_default_phases_rendered_selected(self):
        # the sidebar pre-highlights the two default phases when no param is present
        phase_labels = dict(VALID_PROJECT_PHASES)
        expected = {str(phase_labels[phase]) for phase in DEFAULT_ACTIVE_PROJECT_PHASES}
        selected = {str(choice["display"]) for choice in self._phase_filter_choices() if choice["selected"]}
        self.assertEqual(selected, expected)

    def test_all_option_selected_when_param_empty(self):
        choices = self._phase_filter_choices("?phase=")
        self.assertEqual(str(choices[0]["display"]), "全て")
        self.assertTrue(choices[0]["selected"])
        self.assertFalse(any(choice["selected"] for choice in choices[1:]))


class _AdminFormFieldParser(HTMLParser):
    """Extract name->value for every submittable input/select/textarea in a rendered admin form,
    reproducing what a browser would POST (so an untouched change form round-trips faithfully).
    """

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, str] = {}
        self._select_name: str | None = None
        self._select_value: str | None = None
        self._select_first: str | None = None
        self._textarea_name: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        if tag == "input":
            name = a.get("name")
            itype = (a.get("type") or "text").lower()
            if not name or itype in ("submit", "button", "image", "file"):
                return
            if itype in ("checkbox", "radio"):
                if "checked" in a:
                    self.data[name] = a.get("value", "on")
            else:
                self.data[name] = a.get("value", "")
        elif tag == "select":
            self._select_name = a.get("name")
            self._select_value = None
            self._select_first = None
        elif tag == "option" and self._select_name:
            value = a.get("value", "")
            if self._select_first is None:
                self._select_first = value
            if "selected" in a:
                self._select_value = value
        elif tag == "textarea":
            self._textarea_name = a.get("name")
            if self._textarea_name:
                self.data[self._textarea_name] = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._select_name:
            chosen = self._select_value if self._select_value is not None else (self._select_first or "")
            self.data[self._select_name] = chosen
            self._select_name = None
        elif tag == "textarea":
            self._textarea_name = None

    def handle_data(self, data: str) -> None:
        if self._textarea_name:
            self.data[self._textarea_name] = self.data.get(self._textarea_name, "") + data


class KippoProjectAdminUnderContractChangeViewTestCase(IsStaffModelAdminTestCaseBase):
    """Full change-view POST: flipping the phase to 契約(稼働中) with the default (pre-filled) contract
    must save (the contract is created from its defaults) rather than raising the phase gate.
    """

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        OrganizationMembership.objects.get_or_create(
            user=self.superuser_no_org,
            organization=self.organization,
            defaults={"created_by": self.github_manager, "updated_by": self.github_manager},
        )
        self.project = KippoProject.objects.create(
            organization=self.organization,
            name="under-contract-target",
            category=_global_category("other"),
            columnset=columnset,
            start_date=datetime.date(2026, 1, 1),
            target_date=datetime.date(2026, 6, 30),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_login(self.superuser_no_org)
        self.change_url = reverse("admin:projects_kippoproject_change", args=[self.project.id])

    def _rendered_post_data(self) -> dict:
        response = self.client.get(self.change_url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        parser = _AdminFormFieldParser()
        parser.feed(response.content.decode())
        return parser.data

    def test_change_phase_to_under_contract_with_default_contract_saves(self):
        data = self._rendered_post_data()
        # sanity: the contract inline is pre-filled with the project's dates
        self.assertEqual(data.get("contract-0-start_date"), "2026-01-01")
        self.assertEqual(data.get("contract-0-end_date"), "2026-06-30")
        data["phase"] = PHASE_UNDER_CONTRACT
        data["contract-0-total_amount"] = "1000000"  # fixed pricing requires an amount; keep the default dates
        response = self.client.post(self.change_url, data)
        self.assertEqual(response.status_code, HTTPStatus.FOUND, self._formset_errors(response))
        self.project.refresh_from_db()
        self.assertEqual(self.project.phase, PHASE_UNDER_CONTRACT)
        self.assertIsNotNone(self.project.get_contract())

    def test_change_phase_to_under_contract_with_empty_contract_shows_actionable_message(self):
        # the pre-filled dates alone do not save a contract (no 契約金額 -> the row is skipped): the phase
        # change is blocked with an ACCURATE message on the contract inline, not the misleading dates one.
        data = self._rendered_post_data()
        data["phase"] = PHASE_UNDER_CONTRACT  # leave the contract row at its pre-filled defaults (no amount)
        response = self.client.post(self.change_url, data)
        self.assertEqual(response.status_code, HTTPStatus.OK)  # re-rendered, not saved
        self.assertIn(str(CONTRACT_REQUIRED_FOR_UNDER_CONTRACT_MSG), self._formset_errors(response))
        self.project.refresh_from_db()
        self.assertNotEqual(self.project.phase, PHASE_UNDER_CONTRACT)
        self.assertIsNone(self.project.get_contract())

    @staticmethod
    def _formset_errors(response: HttpResponse) -> str:
        """Collect inline formset errors from a re-rendered (non-redirect) change response, for assert messages."""
        parts = []
        for inline in response.context.get("inline_admin_formsets", []) if response.context else []:
            nonform = inline.formset.non_form_errors()
            if nonform:
                parts.append(f"{inline.formset.prefix}: {nonform.as_text()}")
        return " | ".join(parts)
