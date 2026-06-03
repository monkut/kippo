from commons.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from django import forms
from django.contrib import admin
from django.db import models
from django.db.models import Count, Q
from django.forms import Form
from django.http import request as DjangoRequest  # noqa: N812
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from projects.functions import get_user_session_organization
from projects.models import KippoProject

from customers.models import KippoCustomer


class KippoProjectReadOnlyInline(AllowIsStaffAdminMixin, admin.TabularInline):
    """Read-only list of projects linked to a KippoCustomer (managed via KippoProjectAdmin)."""

    model = KippoProject
    fk_name = "customer"
    extra = 0
    can_delete = False
    verbose_name = _("プロジェクト")
    verbose_name_plural = _("プロジェクト")
    fields = ("get_project_link", "start_date", "target_date", "billing_date")
    readonly_fields = ("get_project_link", "start_date", "target_date", "billing_date")

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None) -> bool:  # No Add button
        return False

    def get_queryset(self, request: DjangoRequest):
        # earliest target_date first
        return super().get_queryset(request).order_by("target_date")

    @admin.display(description=KippoProject._meta.get_field("name").verbose_name)
    def get_project_link(self, obj: KippoProject):
        return format_html('<a href="{}">{}</a>', obj.get_admin_url(), obj.name)


@admin.register(KippoCustomer)
class KippoCustomerAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = ("name", "organization", "email", "get_active_project_count", "display_as_active", "updated_datetime")
    list_display_links = ("name",)
    list_filter = ("organization", "display_as_active")
    search_fields = ("name", "email")
    ordering = ("organization", "-display_as_active", "name")
    fields = ("organization", "name", "email", "phone", "website", "document_url", "notes", "display_as_active")
    inlines = (KippoProjectReadOnlyInline,)

    def get_queryset(self, request: DjangoRequest):
        # Annotate the count of each customer's active (open + display_as_active) projects so
        # the list column is one query and sortable. distinct=True guards against row fan-out
        # from the (non-superuser) organization join below.
        qs = (
            super()
            .get_queryset(request)
            .annotate(
                active_project_count=Count(
                    "projects",
                    filter=Q(projects__is_closed=False, projects__display_as_active=True),
                    distinct=True,
                )
            )
        )
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations).order_by("organization").distinct()

    @admin.display(description=_("アクティブプロジェクト数"), ordering="active_project_count")
    def get_active_project_count(self, obj: KippoCustomer) -> int:
        return obj.active_project_count

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
