import urllib.parse

from commons.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from django import forms
from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.db import models
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.forms import Form
from django.http import request as DjangoRequest  # noqa: N812
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from projects.admin import RETURN_TO_PARAM
from projects.functions import get_user_session_organization
from projects.models import KippoProject

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
    list_display = ("name", "get_active_project_count", "get_compliance_verified", "display_as_active", "updated_datetime")
    list_display_links = ("name",)
    list_filter = ("organization", "display_as_active")
    search_fields = ("name", "email")
    fields = ("organization", "name", "email", "phone", "website", "document_url", "notes", "display_as_active")
    inlines = (KippoProjectReadOnlyInline,)

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

    def get_queryset(self, request: DjangoRequest):
        # Annotate each customer's active (open + display_as_active) project count so the
        # get_active_project_count column can render and sort on the annotation name. The count is a
        # scalar Subquery (see ACTIVE_PROJECT_COUNT), so there is no join fan-out to dedup.
        qs = (
            super()
            .get_queryset(request)
            # select_related the OneToOne compliance_check so the 反社チェック column does not
            # issue one extra query per row in the changelist.
            .select_related("compliance_check")
            .annotate(active_project_count=ACTIVE_PROJECT_COUNT)
        )
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations).distinct()

    @admin.display(description=_("アクティブプロジェクト数"), ordering="active_project_count")
    def get_active_project_count(self, obj: KippoCustomer) -> int:
        return obj.active_project_count

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
