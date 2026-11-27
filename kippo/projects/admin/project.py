"""KippoProject admins (all / active / sales), the project category admin and the contract admin."""

import csv
import logging
import urllib.parse
from string import ascii_lowercase
from typing import TYPE_CHECKING

import nested_admin
from accounts.models import KippoOrganization, KippoUser
from commons.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from commons.functions import local_date
from commons.viewsets import organization_ids_for_user
from customers.models import KippoCustomer
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import Case, Model, OuterRef, Prefetch, QuerySet, Subquery, Sum, Value, When
from django.forms import BaseFormSet, Form
from django.http import (
    HttpResponse,
    HttpResponseRedirect,
    request as DjangoRequest,  # noqa: N812
)
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from tasks.models import KippoTaskStatus

from ..definitions import (
    NON_PROJECT_CATEGORY_VALUE,
    ProjectProgressStatus,
)
from ..filters import CategoryExcludeListFilter, PhaseMultiSelectListFilter
from ..functions import (
    generate_projectstatuscomments_csv,
    get_kippoproject_taskstatus_csv_rows,
    get_user_session_organization,
)
from ..models import (
    _COMPUTE,
    ACTIVE_PROJECT_PHASE_RANK,
    PHASE_COMPLETED,
    PHASE_CONFIDENCE,
    UNRANKED_PROJECT_PHASE_RANK,
    ActiveKippoProject,
    KippoProject,
    KippoProjectBillingEntry,
    KippoProjectContract,
    KippoProjectOrganizationCategory,
    KippoProjectStatus,
    KippoProjectUserStatisfactionResult,
    ProjectAssignmentRate,
    ProjectParticipant,
    ProjectWeeklyEffort,
    SalesKippoProject,
)
from ..services.participants import apply_defaults_to_remaining_months, create_remaining_assignments
from ..services.progress import public_holidays_by_country

if TYPE_CHECKING:
    from ..services.forecast import ForecastResult
from .actions import (
    add_calendar_links_to_slack_channels_action,
    close_kippoproject_action,
    collect_project_github_repositories_action,
    create_github_organizational_project_action,
    create_github_repository_milestones_action,
    fill_remaining_participant_months_action,
    reopen_kippoproject_action,
    request_employee_survey_action,
)
from .assignment_rates import _default_assignment_rate_initial
from .definitions import (
    CONTINUATION_SOURCE_PARAM,
    CONTINUATION_SOURCE_VALUE,
    FULL_CONFIDENCE,
    PROJECT_CLOSURE_FIELDS,
    RETURN_TO_PARAM,
    SURVEY_PROJECT_AUTOCOMPLETE_SCOPE_PARAM,
)
from .inlines import (
    GithubRepositoryProjectInline,
    KippoProjectBillingEntryInline,
    KippoProjectContractInline,
    KippoProjectPhaseConfidenceChangeInline,
    KippoProjectStatusAdminInline,
    KippoProjectStatusReadOnlyInine,
    ProjectAssignmentRateInline,
    ProjectParticipantInline,
    ProjectWeeklyEffortAdminInline,
    ProjectWeeklyEffortReadOnlyInine,
)
from .widgets import _customer_autocomplete_widget, _project_manager_autocomplete_widget, survey_project_display_label, survey_project_queryset

logger = logging.getLogger(__name__)


class KippoProjectAdminForm(forms.ModelForm):
    # Required at project registration (kippo#40 / T19; slimmed for the contract-driven flow) —
    # enforced create-only so existing rows/edits are unaffected. The model keeps these fields
    # nullable; they are marked required on the add form (see __init__) so each renders with the
    # required marker and is validated per-field. name/organization are NOT NULL and category/phase
    # carry model defaults (already enforced by the model/ModelForm). Everything else — PM,
    # target_date, problem_definition, estimates, the contract — is added on a later edit.
    REQUIRED_AT_REGISTRATION = (
        "customer",
        "start_date",
    )

    class Meta:
        model = KippoProject
        exclude = ()  # noqa: DJ006 (admin form inherits field config from ModelAdmin)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # KippoProject.id is a UUID with a default, so .pk is set even before save — use _state.adding
        # to detect a genuine registration (add) vs an edit of an existing row. On add the registration
        # fields are required (asterisk + field-level validation); on edit they stay optional so an
        # existing customer-less / date-less project still saves.
        if self.instance._state.adding:
            for field in self.REQUIRED_AT_REGISTRATION:
                if field in self.fields:
                    self.fields[field].required = True
        else:
            # On the change form the 契約(稼働中) gate is enforced on the contract inline (validated against
            # the contract submitted in the same request), so defer the model-level phase gate to avoid a
            # duplicate message on the phase field and to allow supplying the contract in the same save.
            # The contract inline is hidden on /add/, so the model gate still applies there.
            self.instance._admin_defers_contract_gate = True

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        # allocated_staff_days must be a positive estimate when registering a real, non-closed project
        # at full confidence — confidence (確度) is derived from the submitted phase. Non-project
        # categories (internal/overhead buckets) are exempt (kippo#41). Create-only: editing an
        # existing project must not be blocked by a missing estimate, so a later /change/ (e.g. adding
        # a project-status comment) still saves even when allocated_staff_days is blank.
        phase = cleaned_data.get("phase")
        allocated_staff_days = cleaned_data.get("allocated_staff_days")
        is_non_project = getattr(category, "key", None) == NON_PROJECT_CATEGORY_VALUE
        needs_estimate = (
            self.instance._state.adding and not self.instance.is_closed and not is_non_project and PHASE_CONFIDENCE.get(phase) == FULL_CONFIDENCE
        )
        # the slim /add/ form does not render allocated_staff_days, so the requirement can't apply
        # there (add_error on an absent field would raise); the full add form (continuation close-wizard)
        # does render it, so registration through that path is still validated.
        if "allocated_staff_days" not in self.fields:
            needs_estimate = False
        if needs_estimate and (allocated_staff_days is None or allocated_staff_days <= 0):
            self.add_error(
                "allocated_staff_days",
                _("確度が100%（フェーズ 契約(稼働中) / 完了）の場合は正の値が必須です。"),
            )
        organization = cleaned_data.get("organization")
        submitted_parent_project = cleaned_data.get("parent_project")
        # parent_project is NOT required for a 継続 (continuation) project. It is auto-populated by the
        # close/upsell continuation wizard (_build_continuation_prefill_params) and left optional on the
        # manual add form, so リード=継続 without a parent is allowed. When a parent IS supplied on /add/,
        # it must belong to the new project's organization. (On /change/, parent_project is readonly so
        # it isn't in submitted data — skip this check.)
        if submitted_parent_project and organization and submitted_parent_project.organization_id != organization.id:
            self.add_error(
                "parent_project",
                _("親プロジェクトはこのプロジェクトと同じ組織に属している必要があります。"),
            )
        return cleaned_data


def _format_estimated_completion(result: "ForecastResult") -> str:
    """Render the forecast result as a one-line admin display string."""
    date = result.estimated_completion_date
    if date is None:
        return str(_("(not estimable — no future assignments or insufficient data)"))
    delta = result.delta_from_target_date_days
    if delta is None:
        return date.isoformat()
    if delta == 0:
        return f"{date.isoformat()} (on target)"
    if delta > 0:
        return f"{date.isoformat()} ({delta} days behind target)"
    return f"{date.isoformat()} ({-delta} days ahead of target)"


@admin.register(KippoProjectOrganizationCategory)
class KippoProjectOrganizationCategoryAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    """Staff manage their own organizations' categories; the global (organization=null) template is superuser-only.

    Mirrors the API rule (``IsSuperuserOrOrgMemberForCategory``, kippo#48): a non-superuser staff user
    may add/edit/delete rows only for organizations they are a member of (``organization_ids_for_user``);
    only a superuser may create or modify a global template row or another organization's rows.
    Non-superusers see only their own organizations' categories in the changelist — never the global
    template, never another organization's rows — so they cannot change, delete, or bulk-delete them (kippo#49).
    """

    list_display = ("key", "label", "organization", "sort_order", "is_active", "is_default")
    list_filter = ("is_active", "is_default", "organization")
    search_fields = ("key", "label", "organization__name")
    ordering = ("organization", "sort_order", "key")

    def get_queryset(self, request: DjangoRequest):
        queryset = super().get_queryset(request)
        if not request.user.is_superuser:
            # scope to the user's organizations -> also excludes global (organization=null) template rows,
            # so non-superusers cannot see/change/delete/bulk-delete them or other orgs' rows
            queryset = queryset.filter(organization__in=organization_ids_for_user(request.user))
        return queryset

    def _manageable_by(self, request: DjangoRequest, obj: models.Model | None) -> bool:
        """Non-superusers may only manage rows for organizations they belong to (globals excluded)."""
        if request.user.is_superuser or obj is None:
            return True
        return obj.organization_id in organization_ids_for_user(request.user)

    def has_change_permission(self, request: DjangoRequest, obj: models.Model | None = None):
        if not self._manageable_by(request, obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request: DjangoRequest, obj: models.Model | None = None):
        if not self._manageable_by(request, obj):
            return False
        return super().has_delete_permission(request, obj)

    def formfield_for_foreignkey(self, db_field: models.ForeignKey, request: DjangoRequest, **kwargs):
        # limit the organization picker to the user's organizations (superusers keep the full list)
        if db_field.name == "organization" and not request.user.is_superuser:
            kwargs["queryset"] = KippoOrganization.objects.filter(pk__in=organization_ids_for_user(request.user))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request: DjangoRequest, obj: KippoProjectOrganizationCategory, form: forms.ModelForm, change: bool) -> None:
        # has_add_permission cannot see the object; block creating/moving a row into a global template or an
        # organization the user does not belong to here.
        if not self._manageable_by(request, obj):
            raise PermissionDenied(
                _("You may only create or edit categories for your own organizations; global (template) categories are superuser-only.")
            )
        super().save_model(request, obj, form, change)


class KippoProjectBaseAdmin(AllowIsStaffAdminMixin, nested_admin.NestedModelAdmin, UserCreatedBaseModelAdmin):
    """Shared config for the two registered project admins. Not registered itself.

    Subclassed by KippoProjectAdmin (all projects) and ActiveKippoProjectAdmin (active only, via
    the proxy's ActiveKippoProjectManager). Both render the same columns/ordering/actions; the
    children only differ in queryset and a couple of list/exclude tweaks.
    """

    form = KippoProjectAdminForm
    # Changelist override adds inline CSS widening the 顧客(customer) and フェーズ(phase) columns so each
    # renders on a single line. Set explicitly (not auto-discovered) so the ActiveKippoProject proxy admin
    # uses the same template too. (App static/ dirs are gitignored here, so an external CSS file can't ship.)
    change_list_template = "admin/projects/kippoproject/change_list.html"
    # Inlines hidden on /add/ (only meaningful once a project exists). Exposed as a class
    # attribute so tests and subclasses can reference the same source of truth.
    HIDDEN_ON_ADD_INLINES = (
        # Assignment rates use fixture defaults on /add/ (seeded in save_model); participants need the
        # project's period and are managed after the project exists — both hidden on the add form. The
        # contract is added on a later edit (registration collects only the slim ADD_FIELDS set).
        ProjectAssignmentRateInline,
        ProjectParticipantInline,
        KippoProjectContractInline,
        GithubRepositoryProjectInline,
        ProjectWeeklyEffortReadOnlyInine,
        ProjectWeeklyEffortAdminInline,
        KippoProjectPhaseConfidenceChangeInline,
        KippoProjectStatusReadOnlyInine,
        KippoProjectStatusAdminInline,
    )
    # /add/ form (kippo#41, slimmed for the contract-driven flow): flat, no sections — registration
    # collects only these fields (the required set plus the optional lead_source); everything else
    # (contract, PM, estimates, …) is added on a later edit. The continuation close-wizard add
    # (?_continuation_source=close) instead gets the full sectioned form (see get_fieldsets) so its
    # prefilled optional fields (parent_project, lead_source, slack, …) render.
    ADD_FIELDS = (
        "organization",
        "customer",
        "name",
        "start_date",
        "phase",
        "category",
        "lead_source",
    )
    # Shared base columns. KippoProjectAdmin appends display_as_active (it lists closed/inactive
    # projects too); the active admin uses this set as-is.
    list_display = (
        "id",
        "get_customer_name",
        "name",
        "phase",
        "category",
        "start_date",
        "target_date",
        "get_contract_billing_type_display",
        "get_contract_total_amount_display",
        "get_projectstatus_display",
        "get_latest_kippoprojectstatus_comment",
        "get_kippoprojectuserstatisfactionresult_usernames",
        "get_projectsurvey_display_url",
        "show_github_project_html_url",
    )
    list_display_links = ("id", "name")
    # Reverse OneToOne 'contract' pulled in the same query so the contract columns above don't
    # cost a query per changelist row.
    list_select_related = ("customer", "category", "organization", "contract")
    search_fields = ("id", "name", "phase", "category__key", "category__label", "problem_definition", "customer__name")
    # 顧客 (customer) is selected via a searchable autocomplete (searches/displays KippoCustomer.name
    # through KippoCustomerAdmin.search_fields) instead of a long unsearchable <select>.
    # プロジェクトマネージャー likewise, searching username/first_name/last_name/email through
    # KippoUserAdmin.search_fields (inherited from django's UserAdmin).
    autocomplete_fields = ("customer", "project_manager")
    # Changelist ordering lives in get_ordering() (a Case() expression), not the `ordering`
    # attribute — see the note there for why the attribute can't express it.
    actions = [
        create_github_organizational_project_action,
        create_github_repository_milestones_action,
        collect_project_github_repositories_action,
        close_kippoproject_action,
        reopen_kippoproject_action,
        fill_remaining_participant_months_action,
        add_calendar_links_to_slack_channels_action,
        request_employee_survey_action,
        "export_project_kippotaskstatus_csv",
        "export_kippoprojectstatus_comments_csv",
        "generate_billing_entries",
    ]
    exclude = ("is_closed", "actual_date", "display_as_active", "display_in_project_report")
    # Change-form layout (kippo#41). The /add/ form is flat + required-only (see ADD_FIELDS /
    # get_fieldsets). Closure/survey fields (managed by the close action) and always-hidden fields
    # (columnset, display_as_active, …) are omitted here and enforced via get_exclude.
    fieldsets = [
        (
            None,
            {
                "fields": (
                    "organization",
                    "customer",
                    "name",
                    "phase",
                    "category",
                    "lead_source",
                    "project_manager",
                    "problem_definition",
                    # computed readonly MTG-calendar displays (kippo#13) — surfaced at the top, below the
                    # problem definition. Excluded on /add/ (only meaningful once the project exists).
                    "meeting_calendar_url_field",
                    "meeting_description_tag_field",
                )
            },
        ),
        (
            _("Dates & Estimates"),
            {
                "fields": (
                    "start_date",
                    "target_date",
                    "allocated_staff_days",
                    "estimated_completion_date",
                ),
            },
        ),
        (
            _("Details"),
            {
                "classes": ("collapse",),
                "fields": (
                    "parent_project",
                    "document_folder_url",
                    "slack_channel_name",
                    "slack_notification_channel_name",
                    "enable_cost_report",
                    "docbase_tag",
                    "github_project_html_url",
                    "github_project_api_nodeid",
                ),
            },
        ),
    ]
    inlines = [
        # Milestones not used atm, commenting out.
        # KippoMilestoneReadOnlyInline,
        # KippoMilestoneAdminInline,
        ProjectAssignmentRateInline,
        ProjectParticipantInline,
        # 契約 is rendered directly below the "Dates & Estimates" fieldset by the change_form template
        # (Django emits all fieldsets before any inline, so this list order can't place it there).
        KippoProjectContractInline,
        GithubRepositoryProjectInline,
        ProjectWeeklyEffortReadOnlyInine,
        ProjectWeeklyEffortAdminInline,
        KippoProjectPhaseConfidenceChangeInline,
        KippoProjectStatusReadOnlyInine,
        KippoProjectStatusAdminInline,
    ]
    # copy-to-clipboard handler + toast for the MTG calendar link readonly fields (kippo#13)
    # lives in templates/admin/projects/kippoproject/change_form.html
    change_form_template = "admin/projects/kippoproject/change_form.html"

    def has_add_permission(self, request: DjangoRequest, obj: KippoProject | None = None):  # No Add button
        # check if user has organization memberships
        # - if not can't add new projects
        return request.user.memberships.exists()

    def has_delete_permission(self, request: DjangoRequest, obj: Model | None = None):
        # Remove the delete button from the change page (bulk delete from the changelist stays).
        if "/change/" in request.path:
            return False
        return super().has_delete_permission(request, obj)

    def get_inlines(self, request: DjangoRequest, obj: KippoProject | None = None):
        inlines = list(super().get_inlines(request, obj))
        if obj is None:
            inlines = [cls for cls in inlines if cls not in self.HIDDEN_ON_ADD_INLINES]
        return inlines

    def get_exclude(self, request: DjangoRequest, obj: KippoProject | None = None):
        excluded = list(super().get_exclude(request, obj) or ())
        if obj is None:
            # MTG calendar links + survey/close fields are only meaningful once a project exists
            for fieldname in (*PROJECT_CLOSURE_FIELDS, "meeting_calendar_url_field", "meeting_description_tag_field"):
                if fieldname not in excluded:
                    excluded.append(fieldname)
        if not request.user.is_superuser and "github_project_api_nodeid" not in excluded:
            excluded.append("github_project_api_nodeid")
        # columnset is never an admin choice — it is auto-assigned to the organization's default
        # in save_model (see below). Hidden from the form for every user.
        if "columnset" not in excluded:
            excluded.append("columnset")
        # Once a contract exists its period is the single source of truth (synced onto the project
        # by KippoProjectContract._sync_project_period) — hide the project's own date fields; the
        # contract inline is the editable input.
        if obj is not None and obj.get_contract() is not None:
            for fieldname in ("start_date", "target_date"):
                if fieldname not in excluded:
                    excluded.append(fieldname)
        return tuple(excluded)

    @staticmethod
    def _is_continuation_add(request: DjangoRequest, obj: KippoProject | None) -> bool:
        """True on the continuation close-wizard /add/ (kippo#41): it keeps the full sectioned form so the
        prefilled parent_project + lead_source + inherited fields render and save, unlike the flat plain add.
        """
        return obj is None and request.GET.get(CONTINUATION_SOURCE_PARAM) == CONTINUATION_SOURCE_VALUE

    def get_fieldsets(self, request: DjangoRequest, obj: KippoProject | None = None):
        excluded: set[str] = set(self.get_exclude(request, obj) or ())
        # estimated_completion_date is a computed readonly field, only surfaced for open projects on edit
        if obj is None or obj.is_closed:
            excluded.add("estimated_completion_date")
        # /add/ (kippo#41): flat, required-only, in spec order — EXCEPT the continuation close-wizard add,
        # which prefills optional fields (parent_project, lead_source, slack, …) that must render to be
        # saved, so it gets the full sectioned form below.
        if obj is None and not self._is_continuation_add(request, obj):
            return [(None, {"fields": tuple(f for f in self.ADD_FIELDS if f not in excluded)})]
        # Change form (and continuation-wizard add): the full sectioned layout minus excluded fields. Build a
        # fresh list (never mutate the class attribute); drop now-empty sections; expand collapsed
        # sections on the add path so prefilled fields are visible.
        rebuilt = []
        for label, opts in self.fieldsets:
            fields = tuple(f for f in opts.get("fields", ()) if f not in excluded)
            if not fields:
                continue
            new_opts = {**opts, "fields": fields}
            if obj is None and "collapse" in new_opts.get("classes", ()):
                new_opts["classes"] = tuple(c for c in new_opts["classes"] if c != "collapse")
            rebuilt.append((label, new_opts))
        return rebuilt

    def get_updated_by_display(self, obj: KippoProject) -> str:
        result = ""
        if obj:
            result = obj.updated_by.username
        return result

    get_updated_by_display.short_description = "updated by"

    @admin.display(description=KippoCustomer._meta.verbose_name, ordering="customer__name")
    def get_customer_name(self, obj: KippoProject) -> str:
        return obj.customer.name if obj.customer else ""

    @admin.display(description=_("カテゴリ"), ordering="category__sort_order")
    def get_category_label(self, obj: KippoProject) -> str:
        return obj.category.label if obj.category_id else ""

    def formfield_for_foreignkey(self, db_field: models.Field, request: DjangoRequest, **kwargs):
        # Limit the category select to active categories (global defaults + any organization's custom categories).
        if db_field.name == "category":
            kwargs["queryset"] = KippoProjectOrganizationCategory.objects.filter(is_active=True)
        # Pin the 顧客 autocomplete dropdown to the project's organization (stashed in get_form).
        if db_field.name == "customer" and "customer" in self.get_autocomplete_fields(request):
            kwargs["widget"] = _customer_autocomplete_widget(
                self, db_field, getattr(request, "_customer_autocomplete_organization_id", None), kwargs.get("using")
            )
        # Same for プロジェクトマネージャー — pinned to the project's organization's members.
        if db_field.name == "project_manager" and "project_manager" in self.get_autocomplete_fields(request):
            kwargs["widget"] = _project_manager_autocomplete_widget(
                self, db_field, getattr(request, "_customer_autocomplete_organization_id", None), kwargs.get("using")
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_search_results(self, request: DjangoRequest, queryset: QuerySet, search_term: str) -> tuple[QuerySet, bool]:
        # The employee-survey プロジェクト autocompletes pin their AJAX endpoint to a survey scope via
        # ?survey_scope=<scope> (SurveyScopedProjectAutocompleteSelect). Narrow the dropdown to the same
        # projects the survey form validates against. Unset (the changelist search box, the 顧客 admin's
        # project search) leaves the results untouched.
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        survey_scope = request.GET.get(SURVEY_PROJECT_AUTOCOMPLETE_SCOPE_PARAM)
        if survey_scope:
            # customer joined in: it is part of each result's label (autocomplete_result_label).
            queryset = queryset.filter(pk__in=survey_project_queryset(request.user, survey_scope).values("pk")).select_related("customer")
        return queryset, may_have_duplicates

    @staticmethod
    def autocomplete_result_label(obj: KippoProject) -> str:
        # Read by KippoAutocompleteJsonView. KippoProject.__str__ renders "KippoProject(顧客名 名前)", but the
        # survey forms label the select with "プロジェクト名 顧客名" (label_from_instance) -- without this the
        # option text would change the moment a row is picked from the dropdown.
        return survey_project_display_label(obj)

    @admin.display(description=_("請求方法"), ordering="contract__billing_type")
    def get_contract_billing_type_display(self, obj: KippoProject) -> str:
        contract = obj.get_contract()
        return contract.get_billing_type_display() if contract else ""

    @admin.display(description=_("契約金額"), ordering="contract__total_amount")
    def get_contract_total_amount_display(self, obj: KippoProject) -> str:
        contract = obj.get_contract()
        if contract and contract.total_amount is not None:
            return f"¥{contract.total_amount:,}"
        return ""

    @admin.display(description=_("アンケート完了ユーザ"))
    def get_kippoprojectuserstatisfactionresult_usernames(self, obj: KippoProject | None = None) -> str:
        result = ""
        if obj:
            # Changelist prefetches satisfaction results (get_queryset) to avoid a query per row;
            # fall back to the live query on other admin paths.
            if hasattr(obj, "_changelist_satisfaction_results"):
                usernames = [row.created_by.username for row in obj._changelist_satisfaction_results]
            else:
                usernames = list(
                    KippoProjectUserStatisfactionResult.objects.filter(project=obj)
                    .order_by("created_by__username")
                    .values_list("created_by__username", flat=True)
                )
            result = format_html("<br>".join(usernames))
        return result

    @admin.display(description=_("顧客アンケートURL"))
    def get_projectsurvey_display_url(self, obj: KippoProject) -> str:
        url = obj.get_projectsurvey_url()
        html_encoded_url = ""
        if url:
            html_encoded_url = format_html(f"<a href='{url}'>Survey URL</a>")
        return html_encoded_url

    def export_project_kippotaskstatus_csv(self, request: DjangoRequest, queryset: models.QuerySet):
        """Allow export to csv from admin"""
        if queryset.count() != 1:
            self.message_user(request, _("CSV Export action only supports single Project selection"), level=messages.ERROR)
            return None
        project = queryset[0]
        logger.debug(f"Generating KippoTaskStatus CSV for: {project.name}")
        project_slug = "".join(c for c in project.name.replace(" ", "").lower() if c in ascii_lowercase)
        if not project_slug:
            project_slug = project.id
        filename = f"{project_slug}_{timezone.localtime().strftime('%Y%m%d_%H%M%Z')}.csv"
        logger.debug(f"filename: {filename}")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f"attachment; filename={filename}"
        writer = csv.writer(response)
        try:
            csv_row_generator = get_kippoproject_taskstatus_csv_rows(project, with_headers=True)
            writer.writerows(csv_row_generator)
        except KippoTaskStatus.DoesNotExist:
            display_message = _("No status entries exist for project: %s") % project.name
            self.message_user(request, display_message, level=messages.WARNING)
            return None
        return response

    export_project_kippotaskstatus_csv.short_description = _("Export KippoTaskStatus CSV")

    def export_kippoprojectstatus_comments_csv(self, request: DjangoRequest, queryset: models.QuerySet):
        project_ids = [str(i) for i in queryset.values_list("id", flat=True)]
        if project_ids:
            # initiate creation
            now = timezone.localtime()
            filename = now.strftime("project-statuscomments-%Y%m%d%H%M%S.csv")
            key = f"tmp/download/{filename}"
            generate_projectstatuscomments_csv(project_ids=project_ids, key=key)
            # redirect to waiter
            urlencoded_key = urllib.parse.quote_plus(key)
            backpath_urlencoded_key = urllib.parse.quote_plus(f"{settings.URL_PREFIX}/admin/projects/kippoproject/")
            download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}&back_path={backpath_urlencoded_key}"
            return HttpResponseRedirect(redirect_to=download_waiter_url)
        self.message_user(request, _("No Projects selected!"), level=messages.ERROR)
        return None

    export_kippoprojectstatus_comments_csv.description = _("Download Project Comments CSV")

    @admin.action(description=_("契約から請求エントリを生成"))
    def generate_billing_entries(self, request: DjangoRequest, queryset: models.QuerySet):
        created_count = 0
        skipped_names = []
        for project in queryset:
            contract = project.get_contract()
            project_created_count = len(contract.generate_billing_entries(created_by=request.user)) if contract else 0
            created_count += project_created_count
            if not project_created_count:
                skipped_names.append(project.name)
        if created_count:
            self.message_user(request, _("%d billing entries created.") % created_count, level=messages.INFO)
        if skipped_names:
            self.message_user(
                request,
                _("No entries created for: %s (no contract, contract dates unresolved, or already generated)") % ", ".join(skipped_names),
                level=messages.WARNING,
            )

    @admin.display(description=_("最新コメント"))
    def get_latest_kippoprojectstatus_comment(self, obj: KippoProject):
        result = ""
        # Changelist annotates the latest comment/datetime (get_queryset) to avoid a .latest()
        # per row; fall back to the live query on other admin paths.
        if hasattr(obj, "_changelist_latest_status_comment"):
            comment = obj._changelist_latest_status_comment
            created_datetime = obj._changelist_latest_status_datetime
        else:
            latest_status = obj.get_latest_kippoprojectstatus()
            comment = latest_status.comment if latest_status else None
            created_datetime = latest_status.created_datetime if latest_status else None
        if comment is not None and created_datetime is not None:
            display_date = local_date(created_datetime).strftime("(%m/%d) ")
            spaces = "&nbsp;" * 75
            result = format_html("{display_date}{result}<br/>" + spaces, display_date=display_date, result=comment)
        return result

    @admin.display(description=_("稼働状況"))
    def get_projectstatus_display(self, obj: KippoProject | None = None, total_effort: object = _COMPUTE, holidays: object = _COMPUTE) -> str:
        progress_status_display = "-"
        if obj:
            progress_status_display = None
            project_progress_status: ProjectProgressStatus = obj.get_projectprogressstatus_values(total_effort=total_effort, holidays=holidays)
            # low < high
            # if x > high, then display as "red"
            # if x > low, then display as "yellow"
            # x <= low, then display as "green"
            logger.debug(f"project_progress_status.allocated_effort_hours={project_progress_status.allocated_effort_hours}")
            logger.debug(f"project_progress_status.expected_effort_hours={project_progress_status.expected_effort_hours}")
            if project_progress_status.allocated_effort_hours and project_progress_status.expected_effort_hours:
                low = int(project_progress_status.expected_effort_hours) + 1
                # percent_value = project_progress_status.allocated_effort_hours * (settings.PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD / 100)
                high = (
                    settings.PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD / 100
                ) * project_progress_status.expected_effort_hours + project_progress_status.expected_effort_hours

                max_value = project_progress_status.allocated_effort_hours
                if (
                    project_progress_status.allocated_effort_hours
                    and project_progress_status.current_effort_hours
                    and project_progress_status.allocated_effort_hours < project_progress_status.current_effort_hours
                ):
                    max_value = project_progress_status.current_effort_hours

                difference_percentage = project_progress_status.get_difference_percentage()
                if difference_percentage:
                    difference_percentage_display = f"+{int(difference_percentage)}" if difference_percentage > 0 else f"{int(difference_percentage)}"
                    low_display = f'low="{low}" ' if low < max_value else ""
                    progress_status_display = (
                        f"{project_progress_status.current_effort_hours}h<br/>"
                        f"{difference_percentage_display}%<br/>"
                        f'<meter min="0" '
                        f"{low_display}"
                        f'optimum="{int(project_progress_status.expected_effort_hours)}" '
                        f'high="{high}" '
                        f'max="{max_value}" '
                        f'value="{project_progress_status.current_effort_hours}"></meter>'
                    )
            if not progress_status_display and project_progress_status.current_effort_hours:
                progress_status_display = f"{project_progress_status.current_effort_hours}h"
            elif not progress_status_display:
                progress_status_display = "-"

            # Wrap in link to project status details page
            status_url = f"{settings.URL_PREFIX}/projects/project/{obj.id}/status/"
            progress_status_display = f'<a href="{status_url}">{progress_status_display}</a>'

        return mark_safe(progress_status_display)  # noqa: S308

    @admin.display(description=_("稼働時間"))
    def get_projecteffort_display(self, obj: KippoProject | None = None) -> str:
        result = "-"
        if obj:
            result = obj.get_projecteffort_display()
        return result

    @admin.display(description=_("GITHUBプロジェクト"))
    def show_github_project_html_url(self, obj: KippoProject) -> str:
        if not obj.github_project_html_url:
            return ""
        # Display only the last path component (text after the final "/"), e.g. the project number,
        # while still linking to the full URL.
        label = obj.github_project_html_url.rstrip("/").rsplit("/", 1)[-1]
        return format_html('<a href="{url}">{label}</a>', url=obj.github_project_html_url, label=label)

    def save_formset(self, request: DjangoRequest, form: Form, formset: BaseFormSet, change: bool) -> None:
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()  # ProjectParticipant.delete() also removes its remaining months (kippo#75 X1)
        # Participants whose defaults changed get them applied to their remaining months (kippo#75 E2).
        participants_with_new_defaults = {
            obj.pk
            for obj, changed_fields in formset.changed_objects
            if isinstance(obj, ProjectParticipant) and {"percentage", "role"} & set(changed_fields)
        }
        for instance in instances:
            if instance._state.adding:  # Only for create (needed for handling uuid field as id)
                instance.created_by = request.user  # only update created_by once!
            instance.updated_by = request.user
            # Billing entries can now be edited via the nested inline on the project page; stamp
            # received_by here too (the model save() leaves it for the admin), matching
            # KippoProjectContractAdmin.save_formset so receipts are consistent regardless of page.
            if isinstance(instance, KippoProjectBillingEntry) and instance.is_received and not instance.received_by:
                instance.received_by = request.user
            is_new_participant = isinstance(instance, ProjectParticipant) and instance._state.adding
            instance.save()
            if is_new_participant:
                create_remaining_assignments(instance, request.user)  # kippo#75 R1
            elif instance.pk in participants_with_new_defaults:
                apply_defaults_to_remaining_months(instance, request.user)
        formset.save_m2m()

    def get_form(self, request: DjangoRequest, obj: KippoProject | None = None, **kwargs) -> Form:
        """Set defaults based on request user"""
        # Stash the project's org BEFORE super() builds the form so formfield_for_foreignkey can pin the
        # 顧客 and プロジェクトマネージャー autocomplete dropdowns to it (None on /add/ — no project org yet,
        # dropdowns stay user-scoped).
        request._customer_autocomplete_organization_id = obj.organization_id if obj is not None else None
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)
        # closed projects: every field is readonly, so base_fields is empty — skip the editable-form tweaks
        if obj is not None and obj.is_closed:
            return form
        # PM defaults to the acting user; the slim /add/ form doesn't render the field (added on edit)
        if "project_manager" in form.base_fields:
            form.base_fields["project_manager"].initial = request.user.id
        try:
            user_initial_organization, user_organizations = get_user_session_organization(request)
            user_memberships = request.user.memberships.all()
        except ValueError:
            user_initial_organization = None
            user_memberships = request.user.memberships.none()
        if not user_initial_organization:
            self.message_user(
                request,
                "OrganizationMembership not defined for user! Must belong to an Organization to create a project",
                level=messages.ERROR,
            )
        form.base_fields["organization"].initial = user_initial_organization
        form.base_fields["organization"].queryset = user_memberships
        # On /add/, when the user belongs to exactly one organization there's nothing to choose —
        # preselect it and hide the field. The queryset is still scoped to the user's orgs so a
        # tampered submission gets rejected by the field's normal validation.
        if obj is None and user_initial_organization and len(user_organizations) == 1:
            form.base_fields["organization"].widget = forms.HiddenInput()

        # remove add/change/delete buttons from all ForeignKey fields
        for fieldname in form.base_fields:
            form.base_fields[fieldname].widget.can_add_related = False
            form.base_fields[fieldname].widget.can_change_related = False
            form.base_fields[fieldname].widget.can_delete_related = False

        # parent_project: readonly on the change form (handled in get_readonly_fields). On /add/ it only
        # appears via the continuation close-wizard (kippo#41), where _apply_continuation_source_widgets scopes
        # its queryset; required when lead_source is 継続 — enforced by KippoProjectAdminForm.clean().

        # customer: scope queryset to the project's organization (on change) or the user's orgs (on add).
        self._scope_customer_queryset(form, obj, user_memberships)

        # project_manager: same scoping, so a superuser's dropdown is narrowed too (a non-superuser's is
        # already narrowed by KippoUserAdmin.get_queryset) and a cross-org value is rejected on save.
        self._scope_project_manager_queryset(form, obj, user_memberships)

        # category: scope to the project's organization (on change) or the preselected session
        # organization (on add) so the select never lists other organizations' categories.
        self._scope_category_queryset(form, obj, user_initial_organization)

        # On /add/ the model default is a GLOBAL template row, which the org-scoped queryset above drops;
        # pre-select the initial organization's OWN default category so the required field renders selected
        # and validates without the user opening the dropdown. A ?category= GET prefill
        # still wins via the form's initial data.
        if obj is None and user_initial_organization and "category" in form.base_fields:
            default_category = KippoProjectOrganizationCategory.get_default_for_organization(user_initial_organization.id)
            if default_category:
                form.base_fields["category"].initial = default_category.pk

        # Arriving from the customer admin's "プロジェクトを追加" button (?customer=<pk>): the customer is
        # already chosen, so hide the field. The value is still prefilled (get_changeform_initial_data)
        # and POSTed; the scoped queryset validates it server-side.
        if obj is None and request.GET.get("customer") and "customer" in form.base_fields:
            form.base_fields["customer"].widget = forms.HiddenInput()

        if self._is_continuation_add(request, obj):
            self._apply_continuation_source_widgets(form, user_memberships)
        return form

    @staticmethod
    def _scope_customer_queryset(form: Form, obj: KippoProject | None, user_memberships: models.QuerySet) -> None:
        """Scope the customer queryset to the project's organization (or user's orgs on /add/)."""
        if "customer" not in form.base_fields:
            return
        if obj is not None and obj.organization_id:
            form.base_fields["customer"].queryset = KippoCustomer.objects.filter(organization=obj.organization)
        else:
            form.base_fields["customer"].queryset = KippoCustomer.objects.filter(organization__in=user_memberships)

    @staticmethod
    def _scope_project_manager_queryset(form: Form, obj: KippoProject | None, user_memberships: models.QuerySet) -> None:
        """Scope プロジェクトマネージャー to the project's organization members (or the user's orgs on /add/).

        The currently-assigned PM is always kept selectable — even one who has since left the organization —
        so an unrelated edit can never fail validation on, or silently drop, a value the form did not touch.
        """
        if "project_manager" not in form.base_fields:
            return
        organizations = [obj.organization] if obj is not None and obj.organization_id else user_memberships
        selectable = models.Q(organizationmembership__organization__in=organizations)
        if obj is not None and obj.project_manager_id:
            selectable |= models.Q(pk=obj.project_manager_id)
        form.base_fields["project_manager"].queryset = KippoUser.objects.filter(selectable).distinct()

    @staticmethod
    def _scope_category_queryset(form: Form, obj: KippoProject | None, add_organization: "KippoOrganization | None") -> None:
        """Scope the category select to the project's organization (change) or the preselected /add/ org.

        Narrows the queryset already built by formfield_for_foreignkey. On the change form the currently-
        selected category is always kept selectable — even a legacy global (organization=null) row — so an
        edit can never silently drop it. On /add/ the select is scoped to the single session organization
        (the one preselected in the 組織 field), NOT to every organization the user belongs to: each org
        owns its own identical-label copy of the default category set (kippo#49), so scoping to all
        memberships rendered the same labels once per org — the duplicates in the カテゴリ dropdown.
        """
        if "category" not in form.base_fields:
            return
        queryset = form.base_fields["category"].queryset
        if obj is not None and obj.organization_id:
            form.base_fields["category"].queryset = queryset.filter(models.Q(organization=obj.organization) | models.Q(pk=obj.category_id))
        elif add_organization is not None:
            form.base_fields["category"].queryset = queryset.filter(organization=add_organization)
        else:
            form.base_fields["category"].queryset = queryset.none()

    @staticmethod
    def _apply_continuation_source_widgets(form: Form, user_memberships: models.QuerySet) -> None:
        """Hide parent_project + organization on the continuation close-action redirect.

        Values still POST and the existing validator runs; only the widgets are swapped to hidden.
        parent_project queryset is widened to all of the user's orgs because the default scope
        (user_initial_organization) may not match the parent's org for multi-org users.
        """
        if "parent_project" in form.base_fields:
            form.base_fields["parent_project"].widget = forms.HiddenInput()
            form.base_fields["parent_project"].queryset = KippoProject.objects.filter(organization__in=user_memberships)
        if "organization" in form.base_fields:
            form.base_fields["organization"].widget = forms.HiddenInput()

    def get_readonly_fields(self, request: DjangoRequest, obj: KippoProject | None = None) -> tuple[str, ...]:
        readonly_fields = tuple(super().get_readonly_fields(request, obj))
        # show parent_project as readonly on the change form so admins can see the continuation parent
        if obj is not None and "parent_project" not in readonly_fields:
            readonly_fields = (*readonly_fields, "parent_project")
        # MTG calendar link fields are computed displays — readonly on the change form (kippo#13)
        if obj is not None:
            for fieldname in ("meeting_calendar_url_field", "meeting_description_tag_field"):
                if fieldname not in readonly_fields:
                    readonly_fields = (*readonly_fields, fieldname)
        # forecast on non-closed projects only (per kippo#224 C2 + #226)
        if obj is not None and not obj.is_closed and "estimated_completion_date" not in readonly_fields:
            readonly_fields = (*readonly_fields, "estimated_completion_date")
        # closed projects: lock every editable field — use the re-open action to edit
        if obj is not None and obj.is_closed:
            excluded = set(self.exclude or ())
            locked = tuple(
                f.name
                for f in self.model._meta.get_fields()
                if getattr(f, "editable", False) and not f.auto_created and f.name not in excluded and f.name not in readonly_fields
            )
            readonly_fields = (*readonly_fields, *locked)
        return readonly_fields

    @admin.display(description=_("完了予測日"))
    def estimated_completion_date(self, obj: KippoProject | None = None) -> str:
        from ..exceptions import ProjectStartDateRequiredError
        from ..services.forecast import ProjectAssignmentForecastManager

        if obj is None or obj.pk is None:
            return ""
        try:
            result = ProjectAssignmentForecastManager(obj).compute()
        except ProjectStartDateRequiredError:
            return _("(start_date required)")
        return _format_estimated_completion(result)

    @staticmethod
    def _render_copy_field(copy_text: str, display_html: str) -> str:
        """Render a readonly value, a copy-to-clipboard button, and a polite aria-live status slot.

        The button and the status slot are driven by the handler in change_form.html.
        """
        return format_html(
            '<span class="kippo-copy-field">{}'
            '<button type="button" class="button kippo-copy-button" data-clipboard-text="{}">{}</button>'
            '<span class="kippo-copy-status" role="status" aria-live="polite"></span></span>',
            display_html,
            copy_text,
            _("コピー"),
        )

    @admin.display(description=_("MTG カレンダー作成 URL"))
    def meeting_calendar_url_field(self, obj: KippoProject | None = None) -> str:
        if obj is None or obj._state.adding:
            return ""
        url = obj.get_meeting_calendar_template_url()
        link_html = format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, _("Create Project Meeting"))
        return self._render_copy_field(url, link_html)

    @admin.display(description=_("カレンダーの説明欄"))
    def meeting_description_tag_field(self, obj: KippoProject | None = None) -> str:
        if obj is None or obj._state.adding:
            return ""
        tag = obj.get_dsearch_tag()
        return self._render_copy_field(tag, format_html("<code>{}</code>", tag))

    def get_changeform_initial_data(self, request: DjangoRequest) -> dict:
        initial = super().get_changeform_initial_data(request)
        category = request.GET.get("category")
        if category:
            initial["category"] = category
        parent_project_id = request.GET.get("parent_project")
        if parent_project_id:
            initial["parent_project"] = parent_project_id
        organization_id = request.GET.get("organization")
        if organization_id:
            initial["organization"] = organization_id
        customer_id = request.GET.get("customer")
        if customer_id:
            initial["customer"] = customer_id
        return initial

    def _safe_return_to(self, request: DjangoRequest) -> str | None:
        """The validated `_return_to` admin URL (where the user came from), or None.

        The add/change form posts to its own URL with the query string intact (empty form
        action), so `_return_to` survives into response_add/response_change via request.GET.
        """
        return_to = request.GET.get(RETURN_TO_PARAM)
        if return_to and url_has_allowed_host_and_scheme(return_to, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return return_to
        return None

    def _redirect_back_after_save(self, request: DjangoRequest, response: HttpResponse) -> HttpResponse:
        return_to = self._safe_return_to(request)
        if not return_to:
            return response
        # "Save and add another" / "Save and continue editing" keep the user in the project
        # add/change flow — carry _return_to (and customer/organization prefill) forward so the
        # eventual plain Save still returns to the originating page. Plain Save goes back now.
        if any(key in request.POST for key in ("_addanother", "_continue", "_saveasnew")):
            if isinstance(response, HttpResponseRedirect):
                carry = {RETURN_TO_PARAM: return_to}
                for key in ("customer", "organization"):
                    value = request.GET.get(key)
                    if value:
                        carry[key] = value
                response["Location"] = self._with_params(response["Location"], carry)
            return response
        return HttpResponseRedirect(return_to)

    @staticmethod
    def _with_params(url: str, params: dict[str, str]) -> str:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        query.update({key: [value] for key, value in params.items()})
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))

    def response_add(self, request: DjangoRequest, obj: KippoProject, post_url_continue: str | None = None):
        return self._redirect_back_after_save(request, super().response_add(request, obj, post_url_continue))

    def response_change(self, request: DjangoRequest, obj: KippoProject):
        # Manually flipping an open project's phase to 完了 routes into the close wizard so closure
        # fields (comment / continuation) are captured. The change itself is already saved; the wizard
        # is the offer to finish closing (set in save_model).
        if getattr(request, "_phase_changed_to_completed", False):
            return close_kippoproject_action(self, request, KippoProject.objects.filter(pk=obj.pk))
        return self._redirect_back_after_save(request, super().response_change(request, obj))

    def save_model(self, request: DjangoRequest, obj: KippoProject, form: Form, change: bool):
        # Detect a manual phase→完了 on an open project so response_change can route to the close wizard.
        request._phase_changed_to_completed = (
            change and "phase" in getattr(form, "changed_data", ()) and obj.phase == PHASE_COMPLETED and not obj.is_closed
        )
        if obj.pk is None:
            # expect only not not exist IF creating a new Project via ADMIN
            obj.created_by = request.user
            obj.updated_by = request.user
        else:
            obj.updated_by = request.user

        # columnset is hidden from the form (see get_exclude) — auto-assign the organization's
        # default on create; existing projects keep their stored columnset.
        if obj.columnset_id is None and obj.organization_id:
            default_columnset = obj.organization.get_default_columnset()
            if default_columnset is not None:
                obj.columnset = default_columnset

        super().save_model(request, obj, form, change)

        # The ProjectAssignmentRate inline is hidden on /add/ (see HIDDEN_ON_ADD_INLINES) — seed the
        # per-role fixture defaults the inline used to prefill so a new project still gets its rates.
        if not change:
            self._create_default_assignment_rates(obj, request.user)

    @staticmethod
    def _create_default_assignment_rates(project: KippoProject, user: KippoUser) -> None:
        """Create the default per-role assignment rates for a newly registered project.

        Idempotent against the (project, role) unique_together — only roles not already present are
        created, so a re-save or a continuation copy never duplicates a rate.
        """
        existing_roles = set(project.assignment_rates.values_list("role", flat=True))
        for default in _default_assignment_rate_initial():
            if default["role"] in existing_roles:
                continue
            ProjectAssignmentRate.objects.create(
                project=project,
                role=default["role"],
                rate_per_day=default["rate_per_day"],
                created_by=user,
                updated_by=user,
            )

    def get_ordering(self, request: DjangoRequest):
        # non-project category first, then phase rank (ACTIVE_PROJECT_PHASE_GROUPS: the contracted
        # block, 口頭受注, 提案 高 → 中 → 低, unranked phases last), then confidence desc,
        # target_date asc, name. Self-contained Case expressions (not annotation names) so
        # order_by() resolves them on any queryset — ModelAdmin.get_queryset() applies
        # get_ordering() on the raw manager qs (before any annotate()), and the ChangeList applies
        # it again after get_queryset(). Defining it here (not via the `ordering` attribute) is what
        # makes the changelist actually honor it. KippoProject.name is unique, so this is already a
        # deterministic total ordering.
        return [
            Case(When(category__key="non-project", then=Value(0)), default=Value(1)),
            Case(
                *[When(phase=phase, then=Value(rank)) for phase, rank in ACTIVE_PROJECT_PHASE_RANK.items()],
                default=Value(UNRANKED_PROJECT_PHASE_RANK),
            ),
            "-confidence",
            "target_date",
            "name",
        ]

    def get_queryset(self, request: DjangoRequest):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            qs = qs.filter(organization__in=request.user.organizations).distinct()
        return self._annotate_changelist(qs)

    @staticmethod
    def _annotate_changelist(qs: QuerySet) -> QuerySet:
        """Batch the per-row changelist queries into the list query.

        Replaces three N+1 columns (effort Sum aggregate, latest-status `.latest()`, and the
        satisfaction-result usernames query) with two correlated Subqueries plus one prefetch,
        so the changelist runs a bounded number of queries regardless of row count. The display
        methods read these annotations/prefetch when present and fall back to their live queries
        otherwise (detail view, other admin paths).
        """
        latest_status = KippoProjectStatus.objects.filter(project=OuterRef("pk")).order_by("-created_datetime")
        effort_total = ProjectWeeklyEffort.objects.filter(project=OuterRef("pk")).values("project").annotate(total=Sum("hours")).values("total")
        return qs.annotate(
            _changelist_total_effort=Subquery(effort_total),
            _changelist_latest_status_comment=Subquery(latest_status.values("comment")[:1]),
            _changelist_latest_status_datetime=Subquery(latest_status.values("created_datetime")[:1]),
        ).prefetch_related(
            Prefetch(
                "kippoprojectuserstatisfactionresult_set",
                queryset=KippoProjectUserStatisfactionResult.objects.select_related("created_by").order_by("created_by__username"),
                to_attr="_changelist_satisfaction_results",
            )
        )

    def get_list_display(self, request: DjangoRequest):
        """Bind the status column to a request-scoped per-org holiday cache.

        `get_projectstatus_display` needs each project's org holidays; computing them per row is a
        PublicHoliday query per row. A list_display callable can't see `request`, so we close over
        it here and pass the cached holiday set (built lazily, once per org, per request) plus the
        effort annotation into the display method.
        """
        columns = super().get_list_display(request)

        @admin.display(description=_("稼働状況"))
        def projectstatus_display(obj: KippoProject) -> str:
            holidays = self._changelist_holidays_for(request, obj)
            total_effort = obj._changelist_total_effort if hasattr(obj, "_changelist_total_effort") else _COMPUTE
            return self.get_projectstatus_display(obj, total_effort=total_effort, holidays=holidays)

        return tuple(projectstatus_display if column == "get_projectstatus_display" else column for column in columns)

    def _changelist_holidays_for(self, request: DjangoRequest, obj: KippoProject) -> set:
        """Return (and lazily cache on the request) the org's public-holiday dates.

        The cache is a per-request dict keyed by organization id. Membership is all
        `get_expected_effort` tests, so the full per-country holiday set (a superset of the
        project's own date range) yields identical results while collapsing N per-row queries
        into one query per distinct organization.
        """
        cache = getattr(request, "_changelist_holidays_by_org", None)
        if cache is None:
            cache = {}
            request._changelist_holidays_by_org = cache
        org_id = obj.organization_id
        if org_id not in cache:
            country_id = obj.organization.default_holiday_country_id
            cache[org_id] = public_holidays_by_country([country_id]).get(country_id, set())
        return cache[org_id]


@admin.register(KippoProject)
class KippoProjectAdmin(KippoProjectBaseAdmin):
    # All projects, including closed/inactive — display_as_active is the only column beyond the
    # shared base (it exposes the active/closed state that the active admin filters on).
    list_display = (*KippoProjectBaseAdmin.list_display, "display_as_active")


@admin.register(ActiveKippoProject)
class ActiveKippoProjectAdmin(KippoProjectBaseAdmin):
    # Identical to the base except the queryset: the ActiveKippoProjectManager (proxy default
    # manager) restricts it to open + display_as_active projects. The only form difference is
    # below — closure fields never apply to an active project.
    # Multi-select フェーズ filter, defaulting to the two in-flight phases (kippo new filter).
    # CategoryExcludeListFilter lets the user drop one or more categories from the changelist.
    list_filter = (PhaseMultiSelectListFilter, CategoryExcludeListFilter)

    def get_exclude(self, request: DjangoRequest, obj: KippoProject | None = None):
        excluded: list[str] = list(super().get_exclude(request, obj) or ())
        # Active projects are never closed (filtered by ActiveKippoProjectManager); hide closure fields
        for field in PROJECT_CLOSURE_FIELDS:
            if field not in excluded:
                excluded.append(field)
        # parent_project is only relevant on add (manual continuation creation); hide on change
        if obj is not None and "parent_project" not in excluded:
            excluded.append("parent_project")
        return tuple(excluded)


@admin.register(SalesKippoProject)
class SalesKippoProjectAdmin(KippoProjectBaseAdmin):
    # Pre-contract sales pipeline (SalesKippoProjectManager: open + proposing/verbal phases). Same
    # config as the active admin except the survey/github columns dropped in get_list_display below.
    list_filter = (PhaseMultiSelectListFilter,)
    # Post-delivery columns hidden on the sales changelist — they only carry data once a project ships.
    HIDDEN_LIST_DISPLAY_COLUMNS = (
        "get_kippoprojectuserstatisfactionresult_usernames",  # アンケート完了ユーザ
        "get_projectsurvey_display_url",  # 顧客アンケートURL
        "show_github_project_html_url",  # GITHUBプロジェクト
    )

    def get_list_display(self, request: DjangoRequest):
        # Filter the shared base columns (base binds get_projectstatus_display to the request first).
        columns = super().get_list_display(request)
        return tuple(column for column in columns if column not in self.HIDDEN_LIST_DISPLAY_COLUMNS)


@admin.register(KippoProjectContract)
class KippoProjectContractAdmin(AllowIsStaffAdminMixin, nested_admin.NestedModelAdmin, UserCreatedBaseModelAdmin):
    # Standalone admin so the contract's billing ledger (which belongs to the contract, not the
    # project — kippo#31) can be edited here. The contract itself is also editable as an inline on
    # the project (KippoProjectContractInline); this page adds the billing-entries inline.
    list_display = ("project", "billed_to", "billing_type", "pricing_basis", "total_amount", "estimated_monthly_amount", "start_date", "end_date")
    list_filter = ("billing_type", "pricing_basis")
    list_select_related = ("project", "billed_to")
    search_fields = ("project__name",)
    raw_id_fields = ("project",)
    autocomplete_fields = ("billed_to",)  # searchable 請求先 select, pinned to the project's org in get_form
    inlines = [KippoProjectBillingEntryInline]
    actions = ["generate_billing_entries", "trueup_billing_entries"]

    def get_form(self, request: DjangoRequest, obj: KippoProjectContract | None = None, **kwargs):
        # Stash the contract's project org BEFORE super() builds the form so formfield_for_foreignkey can
        # pin the 請求先 autocomplete dropdown to it (parity with KippoProjectContractInline). On /add/ the
        # project is not yet chosen (raw_id), so the org is unknown → the dropdown falls back to the user's
        # orgs (KippoCustomerAdmin.get_queryset) rather than being project-pinned.
        organization_id = obj.project.organization_id if obj is not None and obj.project_id else None
        request._billed_to_autocomplete_organization_id = organization_id
        form = super().get_form(request, obj, **kwargs)
        # Scope validation to the project's org too, so a tampered cross-org submission is rejected.
        if organization_id and "billed_to" in form.base_fields:
            form.base_fields["billed_to"].queryset = KippoCustomer.objects.filter(organization=organization_id)
        return form

    def formfield_for_foreignkey(self, db_field: models.ForeignKey, request: DjangoRequest, **kwargs):
        # Pin the 請求先 autocomplete dropdown to the contract's project org (stashed in get_form).
        if db_field.name == "billed_to" and "billed_to" in self.get_autocomplete_fields(request):
            kwargs["widget"] = _customer_autocomplete_widget(
                self, db_field, getattr(request, "_billed_to_autocomplete_organization_id", None), kwargs.get("using")
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_formset(self, request: DjangoRequest, form: Form, formset: BaseFormSet, change: bool):
        # Specializes the base created_by/updated_by stamping to also record received_by — the acting
        # admin who marked the entry received (the model save() clears it when un-received).
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if instance.id is None:
                instance.created_by = request.user
            instance.updated_by = request.user
            if instance.is_received and not instance.received_by:
                instance.received_by = request.user
            instance.save()
        formset.save_m2m()

    @admin.action(description=_("契約から請求エントリを生成"))
    def generate_billing_entries(self, request: DjangoRequest, queryset: models.QuerySet):
        created_count = 0
        for contract in queryset:
            created_count += len(contract.generate_billing_entries(created_by=request.user))
        self.message_user(request, _("%d billing entries created.") % created_count, level=messages.INFO)

    @admin.action(description=_("請求エントリを実績に修正"))
    def trueup_billing_entries(self, request: DjangoRequest, queryset: models.QuerySet):
        # kippo#46: correct provisional (仮) effort+monthly entries to logged actuals (実績).
        # Received entries are final and skipped (see KippoProjectContract.trueup_billing_entries).
        updated_count = 0
        for contract in queryset:
            updated_count += contract.trueup_billing_entries(updated_by=request.user)
        self.message_user(request, _("%d billing entries updated to actuals.") % updated_count, level=messages.INFO)
