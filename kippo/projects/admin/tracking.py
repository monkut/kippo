"""Milestone, column set and GitHub issue collection admins."""

from commons.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from django.contrib import admin
from django.http import (
    HttpResponseRedirect,
    request as DjangoRequest,  # noqa: N812
)
from django.utils.translation import gettext_lazy as _

from ..models import (
    CollectIssuesAction,
    KippoMilestone,
    ProjectColumn,
    ProjectColumnSet,
)


@admin.register(KippoMilestone)
class KippoMilestoneAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        "title",
        "get_project_name",
        "get_task_count",
        "is_completed",
        "start_date",
        "target_date",
        "actual_date",
        "updated_by",
        "updated_datetime",
    )
    readonly_fields = ("project",)
    search_fields = ("title", "description")
    ordering = ("project", "target_date")

    def get_project_name(self, obj: KippoMilestone):
        return obj.project.display_label

    get_project_name.short_description = _("Project")

    def get_task_count(self, obj: KippoMilestone) -> int:
        result = 0
        if obj:
            result = obj.kippotask_milestone.count()
        return result

    get_task_count.short_description = _("Task Count")

    def response_add(self, request: DjangoRequest, obj: KippoMilestone, post_url_continue: bool | None = None):
        """Overridding Redirect to the KippoProject page after edit."""
        project_url = obj.project.get_admin_url()
        return HttpResponseRedirect(project_url)

    def response_change(self, request: DjangoRequest, obj: KippoMilestone):
        """Overriding Redirect to the KippoProject page after edit."""
        project_url = obj.project.get_admin_url()
        return HttpResponseRedirect(project_url)

    def get_queryset(self, request: DjangoRequest):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(project__organization__in=request.user.organizations).order_by("project__organization").distinct()


class ProjectColumnInline(admin.TabularInline):
    model = ProjectColumn
    extra = 3


@admin.register(ProjectColumnSet)
class ProjectColumnSetAdmin(UserCreatedBaseModelAdmin):
    list_display = ("name", "get_column_names")
    inlines = [ProjectColumnInline]


@admin.register(CollectIssuesAction)
class CollectIssuesActionAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        "id",
        "organization",
        "start_datetime",
        "end_datetime",
        "status",
        "new_task_count",
        "new_taskstatus_count",
        "updated_taskstatus_count",
    )
