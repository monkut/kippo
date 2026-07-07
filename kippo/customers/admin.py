import datetime
import urllib.parse
from collections.abc import Iterator

from accounts.models import KippoOrganization, KippoUser
from commons.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from commons.functions import is_uuid
from django import forms
from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.contrib.admin.views.main import ChangeList
from django.db import models
from django.db.models import Prefetch
from django.forms import Form
from django.http import request as DjangoRequest  # noqa: N812
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from projects.admin import RETURN_TO_PARAM
from projects.functions import get_user_session_organization
from projects.models import KippoProject, KippoProjectBillingEntry

from customers.functions import (
    ACTIVE_PROJECT_COUNT,
    active_projects_contract_total,
    fiscal_year_org_summaries,
    project_received_total_current_fy,
    shift_fiscal_year,
)
from customers.models import KippoCustomer, KippoCustomerComplianceCheck

# The reusable fiscal-year / summary / per-project computations live in customers.functions (shared
# with KippoCustomerViewSet). This admin formats their raw values as ¥-strings for the changelist.


def _visible_organizations(user: KippoUser) -> models.QuerySet:
    """Organizations in scope for ``user`` on the customer admin — all for a superuser (the changelist
    is not org-scoped for superusers), else the user's own organizations. Keeps the FY header and the
    name filter consistent with the rows shown.
    """
    return KippoOrganization.objects.all() if user.is_superuser else user.organizations


def _yen(amount: object) -> str:
    return f"¥{amount:,.0f}"


class CustomerEndingProjectsFilter(admin.SimpleListFilter):
    """Multi-select customer-name filter listing only customers with 1+ project whose target_date
    (planned completion) falls within the last two fiscal years (previous + current FY) of the
    customer's organization. Keyed on the project's own target_date — not the optional OneToOne
    KippoProjectContract.end_date, which most projects do not have.

    Selecting several customers ORs them (pk__in). Each choice is a toggle link (add/remove the
    customer from the selection) rendered by the default admin/filter.html template — no checkbox
    template needed; ``get_query_string`` emits repeated params (doseq=True).
    """

    title = _("顧客名（直近2会計年度に終了案件あり）")
    parameter_name = "recent_ending_customer"

    def __init__(self, request: DjangoRequest, params: dict, model: type, model_admin: admin.ModelAdmin) -> None:
        super().__init__(request, params, model, model_admin)
        # read all selected values (the param may repeat); super() only keeps the last one. Drop
        # non-UUID values so a tampered query can't raise on the pk__in (UUID) lookup → 500.
        self.selected_values = [value for value in request.GET.getlist(self.parameter_name) if is_uuid(value)]

    def lookups(self, request: DjangoRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        pairs: dict[str, str] = {}
        for organization in _visible_organizations(request.user):
            fiscal_year_start = organization.current_fiscal_year_start()
            # last two fiscal years = previous FY start (one year back) through current FY end (one year forward)
            window_start = shift_fiscal_year(fiscal_year_start, -1)
            window_end = shift_fiscal_year(fiscal_year_start, 1)
            qualifying = (
                KippoCustomer.objects.filter(
                    organization=organization,
                    projects__target_date__gte=window_start,
                    projects__target_date__lt=window_end,
                )
                .distinct()
                .values_list("pk", "name")
            )
            for pk, name in qualifying:
                pairs[str(pk)] = name
        # list customers by name, case-insensitively
        return sorted(pairs.items(), key=lambda item: item[1].casefold())

    def queryset(self, request: DjangoRequest, queryset: models.QuerySet) -> models.QuerySet:
        if self.selected_values:
            return queryset.filter(pk__in=self.selected_values)
        return queryset

    def choices(self, changelist: ChangeList) -> Iterator[dict]:
        selected = set(self.selected_values)
        yield {
            "selected": not selected,
            "query_string": changelist.get_query_string(remove=[self.parameter_name]),
            "display": _("すべて"),
        }
        for pk, name in self.lookup_choices:
            is_selected = pk in selected
            # toggle this customer in/out of the current selection
            remaining = [value for value in self.selected_values if value != pk] if is_selected else [*self.selected_values, pk]
            if remaining:
                query_string = changelist.get_query_string({self.parameter_name: remaining})
            else:
                query_string = changelist.get_query_string(remove=[self.parameter_name])
            yield {"selected": is_selected, "query_string": query_string, "display": name}


class KippoProjectReadOnlyInline(AllowIsStaffAdminMixin, admin.TabularInline):
    """Read-only list of projects linked to a KippoCustomer (managed via KippoProjectAdmin)."""

    model = KippoProject
    fk_name = "customer"
    extra = 0
    can_delete = False
    verbose_name = _("プロジェクト")
    verbose_name_plural = _("プロジェクト")
    fields = ("get_project_link", "start_date", "target_date", "get_contract_type", "get_contract_end_date", "get_contract_total")
    readonly_fields = ("get_project_link", "start_date", "target_date", "get_contract_type", "get_contract_end_date", "get_contract_total")
    # Wraps the default tabular inline and appends a "プロジェクトを追加" link that redirects to the
    # ActiveKippoProject add form (project creation is rich — GitHub project, columnset, etc. — so
    # it is never created inline). Scoped to this inline only; the global tabular template is
    # untouched. The link's href (add_project_url) is built in KippoCustomerAdmin.change_view.
    template = "admin/customers/edit_inline/kippoproject_add_redirect.html"

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None) -> bool:  # No inline add row
        return False

    def get_queryset(self, request: DjangoRequest):
        # earliest target_date first; select_related the OneToOne contract so the contract columns
        # below don't issue a per-project query.
        return super().get_queryset(request).select_related("contract").order_by("target_date")

    @admin.display(description=KippoProject._meta.get_field("name").verbose_name)
    def get_project_link(self, obj: KippoProject):
        return format_html('<a href="{}">{}</a>', obj.get_admin_url(), obj.name)

    @admin.display(description=_("契約種別"))
    def get_contract_type(self, obj: KippoProject) -> str:
        # 請求方法 / 料金体系 (e.g. 納品 / 固定) — both halves describe the contract terms. "-" with no contract.
        contract = getattr(obj, "contract", None)
        if not contract:
            return "-"
        return f"{contract.get_billing_type_display()} / {contract.get_pricing_basis_display()}"

    @admin.display(description=_("契約終了日"))
    def get_contract_end_date(self, obj: KippoProject) -> str:
        contract = getattr(obj, "contract", None)
        return contract.end_date.isoformat() if contract and contract.end_date else "-"

    @admin.display(description=_("契約金額"))
    def get_contract_total(self, obj: KippoProject) -> str:
        # No contract → "-". Effort pricing leaves total_amount blank (billed on actuals) → 実績.
        contract = getattr(obj, "contract", None)
        if not contract:
            return "-"
        return _yen(contract.total_amount) if contract.total_amount is not None else _("実績")


class KippoCustomerComplianceCheckInline(AllowIsStaffAdminMixin, admin.StackedInline):
    """Edit the customer's 反社チェック (compliance check) on the customer page. The record is
    auto-created per customer by a post_save signal, so this inline only edits the existing one.
    """

    model = KippoCustomerComplianceCheck
    extra = 0
    max_num = 1
    can_delete = False
    fields = ("completion_notice", "verified", "verified_datetime", "verified_by", "notes")
    # verified is set only via the changelist actions (反社チェック完了 / 取消), never hand-toggled here, so
    # it and its auto-stamped verified_datetime / verified_by are read-only; the actions own who/when.
    # completion_notice is a read-only reminder rendered when the check is not yet completed. notes stays
    # editable.
    readonly_fields = ("completion_notice", "verified", "verified_datetime", "verified_by")

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None) -> bool:
        return False  # one is auto-created per customer via the post_save signal

    @admin.display(description=_("ステータス"))
    def completion_notice(self, obj: KippoCustomerComplianceCheck | None):
        # Not yet completed → remind the admin to mark it complete via the 「反社チェック完了」 changelist
        # action (the wording mirrors the action label so it is findable). Completed → a check mark.
        if obj and obj.verified:
            return format_html('<span style="color:#447e3c">✓ 反社チェック済み</span>')
        return format_html(
            '<span style="color:#ba2121">{}</span>',
            _("反社チェックが未完了です。完了後、「反社チェック完了」アクションで更新してください。"),
        )


@admin.register(KippoCustomer)
class KippoCustomerAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = ("name", "get_active_project_count", "get_compliance_verified", "updated_datetime")
    list_display_links = ("name",)
    actions = ("mark_compliance_check_completed", "mark_compliance_check_unverified")
    # organization is added conditionally in get_list_filter (only for multi-org members).
    list_filter = (CustomerEndingProjectsFilter,)
    search_fields = ("name", "email")
    fields = ("organization", "name", "email", "phone", "website", "document_url", "contract_folder_url", "notes")
    inlines = (KippoCustomerComplianceCheckInline, KippoProjectReadOnlyInline)
    # changelist template adds the inline script that toggles the per-project detail under the
    # アクティブプロジェクト count (scoped to the changelist; avoids a static-manifest dependency).
    change_list_template = "admin/customers/kippocustomer/change_list.html"

    def get_ordering(self, request: DjangoRequest) -> tuple:
        # Most active customers first. Returns the self-contained Subquery expression (not the
        # active_project_count annotation NAME) so order_by() resolves on ANY queryset — including
        # the bare manager queryset Django builds for FK form fields via get_field_queryset(), which
        # applies this admin's get_ordering(). Ordering by the annotation name would raise FieldError
        # there, and the `ordering` attribute would be rejected by admin check E033.
        return (ACTIVE_PROJECT_COUNT.desc(), "name")

    def get_list_display(self, request: DjangoRequest) -> tuple:
        # Show the organization column only for superusers who belong to more than one organization;
        # non-superusers (org-scoped queryset) and single-org users have nothing to disambiguate.
        if request.user.is_superuser and request.user.organizations.count() > 1:
            return ("name", "organization", *self.list_display[1:])
        return self.list_display

    def get_list_filter(self, request: DjangoRequest) -> tuple:
        # Offer the organization filter only to users who belong to more than one organization;
        # a single-org member has nothing to filter by.
        if request.user.organizations.count() > 1:
            return ("organization", *self.list_filter)
        return self.list_filter

    def get_queryset(self, request: DjangoRequest):
        # Annotate each customer's active (open + display_as_active) project count so the
        # get_active_project_count column can render and sort on the annotation name. The count is a
        # scalar Subquery (see ACTIVE_PROJECT_COUNT), so there is no join fan-out to dedup.
        qs = (
            super()
            .get_queryset(request)
            # select_related the OneToOne compliance_check (反社チェック column) and the organization
            # (its fiscalyear_start_month bounds the received-total sum) so neither issues a per-row query.
            .select_related("compliance_check", "organization")
            .annotate(active_project_count=ACTIVE_PROJECT_COUNT)
            # active projects + their contract + received billing entries back the expandable detail
            # rows of the アクティブプロジェクト column — prefetched to avoid N+1. Only is_received entries
            # are loaded (the detail sums received amounts); the fiscal-year cutoff is applied per-row.
            .prefetch_related(
                Prefetch(
                    "projects",
                    queryset=(
                        KippoProject.objects.filter(is_closed=False, display_as_active=True)
                        .select_related("contract")
                        .prefetch_related(Prefetch("contract__billing_entries", queryset=KippoProjectBillingEntry.objects.filter(is_received=True)))
                        .order_by("name")
                    ),
                    to_attr="active_projects",
                )
            )
        )
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations).distinct()

    @admin.display(description=_("アクティブプロジェクト"), ordering="active_project_count")
    def get_active_project_count(self, obj: KippoCustomer) -> int | str:
        # Default: the count. A non-zero count renders a caret toggle that expands a per-project
        # detail table inline below the count (the toggle script + caret styling are inlined in
        # change_list.html; the detail stays in layout in both states — collapsed via CSS max-height —
        # so expanding never resizes the column). 0 renders plainly (nothing to expand).
        count = obj.active_project_count
        if not count:
            return count
        # Received amounts are summed only from the current fiscal year onward (per the customer's
        # organization fiscalyear_start_month, relative to today in the organization's timezone).
        fiscal_year_start = obj.organization.current_fiscal_year_start()
        active_projects = getattr(obj, "active_projects", ())
        # Σ each active project's contract total (契約金額) — the parenthesised total next to the count.
        contract_total = active_projects_contract_total(list(active_projects))
        rows = format_html_join(
            "",
            "<tr><td>{}</td><td style='text-align:right'>{}</td><td style='text-align:right'>{}</td><td>{}</td></tr>",
            (self._active_project_row(project, fiscal_year_start) for project in active_projects),
        )
        return format_html(
            '<a href="#" class="active-projects-toggle" role="button" aria-expanded="false">'
            '<span class="active-projects-caret" aria-hidden="true"></span>{} ({})</a>'
            '<div class="active-projects-detail">'
            "<table>"
            "<thead><tr><th>{}</th><th>{}</th><th>{}</th><th>{}</th></tr></thead>"
            "<tbody>{}</tbody></table></div>",
            count,
            _yen(contract_total),
            _("プロジェクト"),
            _("入金済合計(今期)"),
            _("契約金額"),
            _("契約終了日"),
            rows,
        )

    @staticmethod
    def _active_project_row(project: KippoProject, fiscal_year_start: datetime.date) -> tuple:
        """One detail row: (project link, current-FY received-billing total, contract amount, end date).

        Entries are pre-filtered to is_received=True (the prefetch); here we keep only those billed on
        or after the fiscal-year start so the total is the current fiscal year's received revenue.
        """
        contract = getattr(project, "contract", None)
        received = project_received_total_current_fy(project, fiscal_year_start)
        name_link = format_html('<a href="{}">{}</a>', project.get_admin_url(), project.name)
        total_display = _yen(contract.total_amount) if contract else "-"
        end_display = contract.end_date.isoformat() if contract and contract.end_date else "-"
        return (name_link, _yen(received), total_display, end_display)

    def changelist_view(self, request: DjangoRequest, extra_context: dict | None = None):
        # Render the per-organization current-fiscal-year summary header AFTER super() so it can be
        # scoped to the customers actually shown (i.e. respecting the active filters).
        response = super().changelist_view(request, extra_context=extra_context)
        if hasattr(response, "context_data") and "cl" in response.context_data:
            response.context_data["fiscal_year_summaries"] = self._fiscal_year_org_summaries(response.context_data["cl"].queryset)
        return response

    @staticmethod
    def _fiscal_year_org_summaries(customers: models.QuerySet) -> list[dict]:
        """Per-organization current-fiscal-year summary for the header, scoped to the (filtered)
        ``customers`` shown on the changelist. The raw per-org aggregates come from the shared
        customers.functions.fiscal_year_org_summaries (the source of truth shared with the API); this
        formats them into the ¥-strings the header template renders.
        """
        return [
            {
                "organization": summary["organization"].name,
                "fiscal_year_start": summary["fiscal_year_start"],
                "fiscal_year_end": summary["fiscal_year_end"],
                "customer_count": summary["customer_count"],
                "project_count": summary["project_count"],
                "received_total_display": _yen(summary["received_total"]),
                "planned_total_display": _yen(summary["planned_total"]),
                "monthly_planned_breakdown": [
                    {"month": row["month"], "amount_display": _yen(row["amount"])} for row in summary["monthly_planned_breakdown"]
                ],
            }
            for summary in fiscal_year_org_summaries(customers)
        ]

    @admin.display(boolean=True, description=_("反社チェック"))
    def get_compliance_verified(self, obj: KippoCustomer) -> bool:
        # The compliance_check is auto-created via signal; guard against its absence anyway.
        compliance_check = getattr(obj, "compliance_check", None)
        return bool(compliance_check and compliance_check.verified)

    @admin.action(description=_("反社チェック完了（ComplianceCheck Completed）"))
    def mark_compliance_check_completed(self, request: DjangoRequest, queryset: models.QuerySet) -> None:
        # Mark each selected customer's 反社チェック completed: stamp the verified flag, the datetime,
        # and the acting admin as verifier. Already-completed checks are left untouched so their
        # original datetime/verifier are preserved.
        now = timezone.now()
        completed = 0
        for customer in queryset:
            compliance_check = getattr(customer, "compliance_check", None)
            if compliance_check is None or compliance_check.verified:
                continue
            compliance_check.verified = True
            compliance_check.verified_datetime = now
            compliance_check.verified_by = request.user
            compliance_check.updated_by = request.user
            compliance_check.save()
            completed += 1
        self.message_user(request, _("反社チェックを完了にしました: %(count)d 件") % {"count": completed})

    @admin.action(description=_("反社チェック取消（ComplianceCheck Unverified）"))
    def mark_compliance_check_unverified(self, request: DjangoRequest, queryset: models.QuerySet) -> None:
        # Reverse a completed 反社チェック: model.save() clears verified_datetime and verified_by when
        # verified is unset. Not-yet-verified checks are skipped so the count reflects real reversals.
        reverted = 0
        for customer in queryset:
            compliance_check = getattr(customer, "compliance_check", None)
            if compliance_check is None or not compliance_check.verified:
                continue
            compliance_check.verified = False
            compliance_check.updated_by = request.user
            compliance_check.save()
            reverted += 1
        self.message_user(request, _("反社チェックを取消しました: %(count)d 件") % {"count": reverted})

    def change_view(self, request: DjangoRequest, object_id: str, form_url: str = "", extra_context: dict | None = None):
        # Surface an "プロジェクトを追加" button under the projects inline that sends the user to the
        # ActiveKippoProject add form (project creation is rich — GitHub project, columnset, etc. —
        # so it is never created inline). The new project is prefilled with this customer + its
        # organization, and _return_to brings the user back to this customer page on save.
        extra_context = extra_context or {}
        customer = self.get_object(request, unquote(object_id))
        if customer is not None:
            # Backfill the 反社チェック record for customers that predate the auto-create signal — without
            # it the inline (extra=0, no add permission) renders an empty section and the completion
            # notice never shows. get_or_create is idempotent for customers that already have one.
            KippoCustomerComplianceCheck.objects.get_or_create(customer=customer)
            params = {
                "customer": str(customer.pk),
                "organization": str(customer.organization_id),
                RETURN_TO_PARAM: request.path,
            }
            extra_context["add_project_url"] = f"{reverse('admin:projects_activekippoproject_add')}?{urllib.parse.urlencode(params)}"
        return super().change_view(request, object_id, form_url, extra_context)

    def get_form(self, request: DjangoRequest, obj: KippoCustomer | None = None, **kwargs) -> Form:
        form = super().get_form(request, obj, **kwargs)
        if "organization" not in form.base_fields:
            return form
        try:
            user_initial_organization, user_organizations = get_user_session_organization(request)
        except ValueError:
            user_initial_organization, user_organizations = None, []
        if not request.user.is_superuser:
            form.base_fields["organization"].queryset = request.user.memberships.all()
        if user_initial_organization:
            form.base_fields["organization"].initial = user_initial_organization
            # Hide the field only for single-org users (there's nothing to choose). Multi-org
            # users keep it visible — initialized to the session org — so they can pick which
            # organization the customer belongs to.
            if len(user_organizations) == 1:
                form.base_fields["organization"].widget = forms.HiddenInput()
        return form
