import datetime
import urllib.parse
from collections import defaultdict
from collections.abc import Iterator

from accounts.models import KippoOrganization, KippoUser
from commons.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from django import forms
from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.contrib.admin.views.main import ChangeList
from django.db import models
from django.db.models import Count, IntegerField, OuterRef, Prefetch, Subquery, Sum
from django.db.models.functions import Coalesce
from django.forms import Form
from django.http import request as DjangoRequest  # noqa: N812
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from projects.admin import RETURN_TO_PARAM
from projects.functions import get_user_session_organization
from projects.models import KippoProject, KippoProjectBillingEntry, KippoProjectContract

from customers.models import KippoCustomer

# A customer's active (open + display_as_active) project count. A correlated Subquery (a scalar
# expression), NOT an aggregate: Django 5.2 forbids ordering by an aggregate that isn't also in
# annotate(), and get_ordering() is applied to bare manager querysets (no annotation) when Django
# builds FK form fields via get_field_queryset(). A scalar Subquery orders correctly anywhere.
# Coalesce(..., 0) so customers with no active projects sort/display as 0 rather than NULL.
ACTIVE_PROJECT_COUNT = Coalesce(
    Subquery(
        KippoProject.objects.filter(customer=OuterRef("pk"), is_closed=False, display_as_active=True)
        .order_by()
        .values("customer")
        .annotate(count=Count("pk"))
        .values("count"),
        output_field=IntegerField(),
    ),
    0,
)


def _shift_fiscal_year(fiscal_year_start: datetime.date, years: int) -> datetime.date:
    """The fiscal-year boundary ``years`` away from ``fiscal_year_start`` (same month, day 1)."""
    return datetime.date(fiscal_year_start.year + years, fiscal_year_start.month, 1)


def _visible_organizations(user: KippoUser) -> models.QuerySet:
    """Organizations in scope for ``user`` on the customer admin — all for a superuser (the changelist
    is not org-scoped for superusers), else the user's own organizations. Keeps the FY header and the
    name filter consistent with the rows shown.
    """
    return KippoOrganization.objects.all() if user.is_superuser else user.organizations


def _yen(amount: object) -> str:
    return f"¥{amount:,.0f}"


class CustomerEndingProjectsFilter(admin.SimpleListFilter):
    """Multi-select customer-name filter listing only customers with 1+ project whose contract ends
    within the last two fiscal years (previous + current FY) of the customer's organization.

    Selecting several customers ORs them (pk__in). Each choice is a toggle link (add/remove the
    customer from the selection) rendered by the default admin/filter.html template — no checkbox
    template needed; ``get_query_string`` emits repeated params (doseq=True).
    """

    title = _("顧客名（直近2会計年度に終了案件あり）")
    parameter_name = "recent_ending_customer"

    def __init__(self, request: DjangoRequest, params: dict, model: type, model_admin: admin.ModelAdmin) -> None:
        super().__init__(request, params, model, model_admin)
        # read all selected values (the param may repeat); super() only keeps the last one
        self.selected_values = request.GET.getlist(self.parameter_name)

    def lookups(self, request: DjangoRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        pairs: dict[str, str] = {}
        for organization in _visible_organizations(request.user):
            fiscal_year_start = organization.current_fiscal_year_start()
            # last two fiscal years = previous FY start (one year back) through current FY end (one year forward)
            window_start = _shift_fiscal_year(fiscal_year_start, -1)
            window_end = _shift_fiscal_year(fiscal_year_start, 1)
            qualifying = (
                KippoCustomer.objects.filter(
                    organization=organization,
                    projects__contract__end_date__gte=window_start,
                    projects__contract__end_date__lt=window_end,
                )
                .distinct()
                .values_list("pk", "name")
            )
            for pk, name in qualifying:
                pairs[str(pk)] = name
        return sorted(pairs.items(), key=lambda item: item[1])

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
    fields = ("get_project_link", "start_date", "target_date")
    readonly_fields = ("get_project_link", "start_date", "target_date")
    # Wraps the default tabular inline and appends a "プロジェクトを追加" link that redirects to the
    # ActiveKippoProject add form (project creation is rich — GitHub project, columnset, etc. — so
    # it is never created inline). Scoped to this inline only; the global tabular template is
    # untouched. The link's href (add_project_url) is built in KippoCustomerAdmin.change_view.
    template = "admin/customers/edit_inline/kippoproject_add_redirect.html"

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None) -> bool:  # No inline add row
        return False

    def get_queryset(self, request: DjangoRequest):
        # earliest target_date first
        return super().get_queryset(request).order_by("target_date")

    @admin.display(description=KippoProject._meta.get_field("name").verbose_name)
    def get_project_link(self, obj: KippoProject):
        return format_html('<a href="{}">{}</a>', obj.get_admin_url(), obj.name)


@admin.register(KippoCustomer)
class KippoCustomerAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = ("name", "get_active_project_count", "get_compliance_verified", "updated_datetime")
    list_display_links = ("name",)
    # organization is added conditionally in get_list_filter (only for multi-org members);
    # the display_as_active filter is intentionally omitted.
    list_filter = (CustomerEndingProjectsFilter,)
    search_fields = ("name", "email")
    fields = ("organization", "name", "email", "phone", "website", "document_url", "notes", "display_as_active")
    inlines = (KippoProjectReadOnlyInline,)
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
        # Default: the count. Clicking a non-zero count toggles a per-project detail table (the
        # toggle script is inlined in change_list.html). 0 renders plainly (nothing to expand).
        count = obj.active_project_count
        if not count:
            return count
        # Received amounts are summed only from the current fiscal year onward (per the customer's
        # organization fiscalyear_start_month, relative to today in the organization's timezone).
        fiscal_year_start = obj.organization.current_fiscal_year_start()
        rows = format_html_join(
            "",
            "<tr><td>{}</td><td style='text-align:right'>{}</td><td style='text-align:right'>{}</td><td>{}</td></tr>",
            (self._active_project_row(project, fiscal_year_start) for project in getattr(obj, "active_projects", ())),
        )
        return format_html(
            '<a href="#" class="active-projects-toggle">{}</a>'
            '<table class="active-projects-detail" hidden>'
            "<thead><tr><th>{}</th><th>{}</th><th>{}</th><th>{}</th></tr></thead>"
            "<tbody>{}</tbody></table>",
            count,
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
        received = sum((entry.amount for entry in contract.billing_entries.all() if entry.billing_date >= fiscal_year_start), 0) if contract else 0
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
        ``customers`` shown on the changelist. Per org: customer count; 'projects planned to complete
        this FY' = those customers' contracts whose end_date falls in the current FY; planned total =
        Σ their total_amount; received total = Σ their received billing-entry amounts.
        """
        customer_pks_by_org: dict = defaultdict(list)
        for customer_pk, organization_id in customers.values_list("pk", "organization_id"):
            customer_pks_by_org[organization_id].append(customer_pk)

        organizations = {org.pk: org for org in KippoOrganization.objects.filter(pk__in=customer_pks_by_org)}
        summaries = []
        for organization_id, customer_pks in customer_pks_by_org.items():
            organization = organizations[organization_id]
            fiscal_year_start = organization.current_fiscal_year_start()
            fiscal_year_end = _shift_fiscal_year(fiscal_year_start, 1)
            contracts = KippoProjectContract.objects.filter(
                project__customer__in=customer_pks,
                end_date__gte=fiscal_year_start,
                end_date__lt=fiscal_year_end,
            )
            # count + planned total are over the same contracts queryset → one aggregate query
            contract_summary = contracts.aggregate(count=Count("pk"), total=Sum("total_amount"))
            received_total = (
                KippoProjectBillingEntry.objects.filter(contract__in=contracts, is_received=True).aggregate(total=Sum("amount"))["total"] or 0
            )
            summaries.append(
                {
                    "organization": organization.name,
                    "fiscal_year_start": fiscal_year_start,
                    "fiscal_year_end": fiscal_year_end,
                    "customer_count": len(customer_pks),
                    "project_count": contract_summary["count"],
                    "received_total_display": _yen(received_total),
                    "planned_total_display": _yen(contract_summary["total"] or 0),
                }
            )
        return sorted(summaries, key=lambda summary: summary["organization"])

    @admin.display(boolean=True, description=_("反社チェック"))
    def get_compliance_verified(self, obj: KippoCustomer) -> bool:
        # The compliance_check is auto-created via signal; guard against its absence anyway.
        compliance_check = getattr(obj, "compliance_check", None)
        return bool(compliance_check and compliance_check.verified)

    def change_view(self, request: DjangoRequest, object_id: str, form_url: str = "", extra_context: dict | None = None):
        # Surface an "プロジェクトを追加" button under the projects inline that sends the user to the
        # ActiveKippoProject add form (project creation is rich — GitHub project, columnset, etc. —
        # so it is never created inline). The new project is prefilled with this customer + its
        # organization, and _return_to brings the user back to this customer page on save.
        extra_context = extra_context or {}
        customer = self.get_object(request, unquote(object_id))
        if customer is not None:
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
