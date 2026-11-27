"""Monthly assignment and monthly cost admins."""

import json

from commons.admin import PrettyJSONWidget, UserCreatedBaseModelAdmin
from commons.viewsets import organization_ids_for_user
from django.contrib import admin, messages
from django.db import models
from django.db.models import JSONField, OuterRef, QuerySet, Subquery, Sum
from django.http import (
    request as DjangoRequest,  # noqa: N812
)
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from ..filters import ParticipantListFilter
from ..models import (
    ProjectMonthlyAssignment,
    ProjectMonthlyCost,
)


def auto_extend_projectmonthlyassignment_action(
    modeladmin: admin.ModelAdmin,
    request: DjangoRequest,
    queryset: models.QuerySet,
) -> None:
    """Run `auto_create_future_assignments` once per distinct project in the selection (kippo#19)."""
    from ..services.autoassign import auto_create_future_assignments

    distinct_projects = {row.project_id: row.project for row in queryset.select_related("project")}
    if not distinct_projects:
        modeladmin.message_user(request, _("No assignments selected."), level=messages.ERROR)
        return

    total_created = 0
    for project in distinct_projects.values():
        created_rows, skip_reason = auto_create_future_assignments(project, request.user)
        total_created += len(created_rows)
        if skip_reason is not None:
            modeladmin.message_user(
                request,
                _("Project '%(name)s' skipped: %(reason)s") % {"name": project.name, "reason": skip_reason.value},
                level=messages.WARNING,
            )
        else:
            modeladmin.message_user(
                request,
                _("Project '%(name)s': created %(count)d future-month rows.") % {"name": project.name, "count": len(created_rows)},
                level=messages.INFO,
            )
    if total_created:
        modeladmin.message_user(
            request,
            _("Total future-month rows created: %(count)d") % {"count": total_created},
            level=messages.SUCCESS,
        )


auto_extend_projectmonthlyassignment_action.short_description = _("将来月の割当を生成")  # noqa: E305


@admin.register(ProjectMonthlyAssignment)
class ProjectMonthlyAssignmentAdmin(UserCreatedBaseModelAdmin):
    list_display = ("project", "get_project_organization", "user", "month", "percentage", "user_assignment_percentage_total", "role")
    list_filter = (ParticipantListFilter, "role", "month", "project__organization")
    list_select_related = ("project__organization", "user")
    ordering = ("project", "user__username", "month")
    search_fields = ("project__name", "user__username")
    exclude = ("is_confirmed",)  # deprecated 確定 field (kippo#69 D3)
    actions = (auto_extend_projectmonthlyassignment_action,)

    def get_list_display(self, request: DjangoRequest) -> tuple:
        columns = super().get_list_display(request)
        if self._has_multiple_organizations(request):
            return columns
        return tuple(column for column in columns if column != "get_project_organization")

    def get_list_filter(self, request: DjangoRequest) -> tuple:
        filters = super().get_list_filter(request)
        if self._has_multiple_organizations(request):
            return filters
        return tuple(list_filter for list_filter in filters if list_filter != "project__organization")

    @staticmethod
    def _has_multiple_organizations(request: DjangoRequest) -> bool:
        """The Organization column / filter only distinguish rows for users in more than one organization."""
        return len(organization_ids_for_user(request.user)) > 1

    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        # The row user's total percentage for the row's month, scoped to the project's organization —
        # the same grouping as the over-allocation warning in ProjectMonthlyAssignment.save().
        month_total = (
            ProjectMonthlyAssignment.objects.filter(
                user=OuterRef("user"),
                month=OuterRef("month"),
                project__organization=OuterRef("project__organization"),
            )
            .values("user")
            .annotate(total=Sum("percentage"))
            .values("total")
        )
        return super().get_queryset(request).annotate(user_assignment_percentage_total=Subquery(month_total))

    @admin.display(description=_("User assignment percentage total"), ordering="user_assignment_percentage_total")
    def user_assignment_percentage_total(self, obj: ProjectMonthlyAssignment) -> int | None:
        return obj.user_assignment_percentage_total

    def get_project_organization(self, obj: ProjectMonthlyAssignment):
        organization_name = obj.project.organization.name
        return organization_name

    get_project_organization.short_description = _("Organization")


@admin.register(ProjectMonthlyCost)
class ProjectMonthlyCostAdmin(admin.ModelAdmin):
    list_display = ("project", "get_project_organization", "month", "service", "cost", "currency")
    list_filter = ("service", "currency")
    search_fields = ("project__id", "project__name")
    ordering = ("project", "-month")
    formfield_overrides = {JSONField: {"widget": PrettyJSONWidget}}

    def get_readonly_fields(self, request: DjangoRequest, obj: ProjectMonthlyCost | None = None) -> tuple:
        if obj:  # Editing existing object
            return ("project", "month", "service", "cost", "currency", "itemized_cost_display")
        return ()

    def get_project_organization(self, obj: ProjectMonthlyCost) -> str:
        return obj.project.organization.name

    get_project_organization.short_description = _("Organization")

    @admin.display(description=_("Itemized Cost"))
    def itemized_cost_display(self, obj: ProjectMonthlyCost) -> str:
        """Display itemized_cost as pretty-printed JSON."""
        if not obj.itemized_cost:
            return "-"
        formatted_json = json.dumps(obj.itemized_cost, indent=2, ensure_ascii=False, sort_keys=True)
        return format_html("<pre style='margin: 0; white-space: pre-wrap;'>{}</pre>", formatted_json)

    def has_module_permission(self, request: DjangoRequest) -> bool:
        return request.user.is_superuser

    def has_view_permission(self, request: DjangoRequest, obj: ProjectMonthlyCost | None = None) -> bool:
        return request.user.is_superuser

    def has_add_permission(self, request: DjangoRequest) -> bool:
        return request.user.is_superuser

    def has_change_permission(self, request: DjangoRequest, obj: ProjectMonthlyCost | None = None) -> bool:
        return request.user.is_superuser

    def has_delete_permission(self, request: DjangoRequest, obj: ProjectMonthlyCost | None = None) -> bool:
        return request.user.is_superuser

    def get_queryset(self, request: DjangoRequest) -> models.QuerySet:
        qs = super().get_queryset(request)
        return qs.filter(project__organization__in=request.user.organizations).order_by("project__organization")
