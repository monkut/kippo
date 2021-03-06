import csv
import logging
from base64 import b64decode
from string import ascii_lowercase

from common.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from django.contrib import admin, messages
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import ugettext_lazy as _
from ghorgs.managers import GithubOrganizationManager
from tasks.models import KippoTaskStatus
from tasks.periodic.tasks import collect_github_project_issues

from .functions import get_kippoproject_taskstatus_csv_rows, get_user_session_organization
from .models import (
    ActiveKippoProject,
    CollectIssuesAction,
    GithubMilestoneAlreadyExists,
    KippoMilestone,
    KippoProject,
    KippoProjectStatus,
    ProjectAssignment,
    ProjectColumn,
    ProjectColumnSet,
)

logger = logging.getLogger(__name__)


class KippoMilestoneReadOnlyInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoMilestone
    extra = 0
    fields = ("title", "start_date", "target_date", "actual_date", "allocated_staff_days", "description")
    readonly_fields = ("title", "start_date", "target_date", "actual_date", "allocated_staff_days", "description")

    def has_add_permission(self, request, obj):  # No Add button
        return False

    def get_queryset(self, request):
        # order milestones as expected
        qs = super().get_queryset(request).order_by("target_date")
        return qs


class KippoMilestoneAdminInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoMilestone
    extra = 0
    fields = ("title", "start_date", "target_date", "actual_date", "allocated_staff_days", "description")

    def get_queryset(self, request):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


class KippoProjectStatusReadOnlyInine(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoProjectStatus
    extra = 0
    fields = ("created_datetime", "created_by", "comment")
    readonly_fields = ("created_datetime", "created_by", "comment")

    def has_add_permission(self, request, obj):  # No Add button
        return False

    def get_queryset(self, request):
        # order milestones as expected
        qs = super().get_queryset(request).order_by("created_datetime")
        return qs


class KippoProjectStatusAdminInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoProjectStatus
    extra = 1
    fields = ("comment",)

    def get_queryset(self, request):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


def create_github_organizational_project_action(modeladmin, request, queryset) -> None:
    """
    Admin Action command to create a github organizational project from the selected KippoProject(s)

    Where an existing Github Organization project does not exist (not assigned)
    """
    successful_creation_projects = []
    skipping = []
    for kippo_project in queryset:
        if kippo_project.github_project_html_url:
            message = f"{kippo_project.name} already has GitHub Project set ({kippo_project.github_project_html_url}), SKIPPING!"
            logger.warning(message)
            skipping.append(message)
        else:
            if not kippo_project.columnset:
                modeladmin.message_user(
                    request, message=f"ProjectColumnSet not defined for {kippo_project}, cannot create Github Project!", level=messages.ERROR
                )
                return

            columns = kippo_project.get_column_names()
            github_organization_name = kippo_project.organization.github_organization_name
            githubaccesstoken = kippo_project.organization.githubaccesstoken
            github_manager = GithubOrganizationManager(organization=github_organization_name, token=githubaccesstoken.token)
            # create the organizational project in github
            # create_organizational_project(organization: str, name: str, description: str, columns: list=None) -> Tuple[str, List[object]]:
            url, responses = github_manager.create_organizational_project(
                name=kippo_project.github_project_name, description=kippo_project.github_project_description, columns=columns
            )
            kippo_project.github_project_html_url = url
            logger.debug(f"kippo_project.github_project_html_url={url}")
            logger.debug(f"github_manager.create_organizational_project() responses: {responses}")
            # project_id appears to be a portion of the returned node_id when decoded from base64
            # -- NOTE: not officially supported by github but seems to be the current implementation
            # https://developer.github.com/v3/projects/#get-a-project
            github_project_id = None
            column_info = []
            for item in responses:
                if isinstance(item, dict):  # get "project_id"
                    if "createProject" in item["data"]:
                        project_encoded_id = item["data"]["createProject"]["project"]["id"]
                        decoded_id = b64decode(project_encoded_id).decode("utf8").lower()
                        # parse out project id portion
                        github_project_id = decoded_id.split("project")[-1]
                elif isinstance(item, list):  # get column_info
                    for column_response in item:
                        if "addProjectColumn" in column_response["data"]:
                            column_info.append(column_response["data"]["addProjectColumn"]["columnEdge"]["node"])

            kippo_project.github_project_api_url = f"https://api.github.com/projects/{github_project_id}"
            kippo_project.column_info = column_info
            kippo_project.save()
            successful_creation_projects.append((kippo_project.name, url, columns))
    if skipping:
        for m in skipping:
            modeladmin.message_user(request, message=m, level=messages.WARNING)
    if successful_creation_projects:
        modeladmin.message_user(
            request, message=f"({len(successful_creation_projects)}) GitHub Projects Created: {successful_creation_projects}", level=messages.INFO
        )


create_github_organizational_project_action.short_description = _("Create Github Organizational Project(s) for selected")  # noqa: E305


def create_github_repository_milestones_action(modeladmin, request, queryset) -> None:
    """
    Admin Action command to create a github repository milestones for ALL
    repositories linked to the selected KippoProject(s).
    """
    for kippo_project in queryset:
        milestones = kippo_project.active_milestones()
        for milestone in milestones:
            try:
                created_octocat_milestones = milestone.update_github_milestones(request.user)
                for created, created_octocat_milestone in created_octocat_milestones:
                    modeladmin.message_user(
                        request,
                        message=f"({kippo_project.name}) {created_octocat_milestone.repository.name} created milestone: "
                        f"{milestone.title} ({milestone.start_date} - {milestone.target_date})",
                        level=messages.INFO,
                    )
            except GithubMilestoneAlreadyExists as e:
                modeladmin.message_user(
                    request, message=f"({kippo_project.name}) Failed to create milestone for related repository(ies): {e.args}", level=messages.ERROR
                )


create_github_repository_milestones_action.short_description = _("Create related Github Repository Milestone(s) for selected")  # noqa: E305


def collect_project_github_repositories_action(modeladmin, request, queryset) -> None:
    """
    Admin action to collect the github repositories for selected KippoProjects
    Calls `()` which also updates issues on collection
    """
    # get request user organization
    organization, user_organizations = get_user_session_organization(request)

    # collect project github html_urls to filter for the collect_github_project_issues functoin
    github_project_html_urls_to_update = []
    for kippoproject in queryset.filter(organization__in=user_organizations):  # apply filter to only access user accessible orgs
        logger.debug(f"adding project: {kippoproject}")
        github_project_html_urls_to_update.append(kippoproject.github_project_html_url)

    collect_github_project_issues(1, kippo_organization_id=str(organization.id), github_project_html_urls=github_project_html_urls_to_update)
    modeladmin.message_user(
        request, message=f"({len(github_project_html_urls_to_update)}) KippoProjects updated from GitHub Organizational Projects", level=messages.INFO
    )


collect_project_github_repositories_action.short_description = _("Collect Project Repositories")  # noqa


@admin.register(KippoProject)
class KippoProjectAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        "id",
        "name",
        "phase",
        "category",
        "get_confidence_display",
        "updated_by",
        "get_latest_kippoprojectstatus_comment",
        "start_date",
        "target_date",
        "get_projectsurvey_display_url",
        "show_github_project_html_url",
        "display_as_active",
        "updated_datetime",
    )
    list_display_links = ("id", "name")
    search_fields = ("name", "phase", "category", "problem_definition")
    ordering = ("organization", "-display_as_active", "-confidence", "phase", "name")
    actions = [
        create_github_organizational_project_action,
        create_github_repository_milestones_action,
        collect_project_github_repositories_action,
        "export_project_kippotaskstatus_csv",
    ]
    inlines = [KippoMilestoneReadOnlyInline, KippoMilestoneAdminInline, KippoProjectStatusReadOnlyInine, KippoProjectStatusAdminInline]

    def get_confidence_display(self, obj):
        result = ""
        if obj.confidence:
            result = f"{obj.confidence} %"
        return result

    get_confidence_display.admin_order_field = "confidence"
    get_confidence_display.short_description = "confidence"

    def get_projectsurvey_display_url(self, obj):
        url = obj.get_projectsurvey_url()
        html_encoded_url = ""
        if url:
            html_encoded_url = format_html(f"<a href='{url}'>Survey URL</a>")
        return html_encoded_url

    get_projectsurvey_display_url.short_description = _("Project Survey URL")

    def export_project_kippotaskstatus_csv(self, request, queryset):
        """Allow export to csv from admin"""
        if queryset.count() != 1:
            self.message_user(request, _("CSV Export action only supports single Project selection"), level=messages.ERROR)
        else:
            project = queryset[0]
            logger.debug(f"Generating KippoTaskStatus CSV for: {project.name}")
            project_slug = "".join(c for c in project.name.replace(" ", "").lower() if c in ascii_lowercase)
            if not project_slug:
                project_slug = project.id
            filename = f'{project_slug}_{timezone.now().strftime("%Y%m%d_%H%M%Z")}.csv'
            logger.debug(f"filename: {filename}")
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f"attachment; filename={filename}"
            writer = csv.writer(response)
            try:
                csv_row_generator = get_kippoproject_taskstatus_csv_rows(project, with_headers=True)
                writer.writerows(csv_row_generator)
                return response
            except KippoTaskStatus.DoesNotExist:
                self.message_user(request, _(f"No status entries exist for project: {project.name}"), level=messages.WARNING)

    export_project_kippotaskstatus_csv.short_description = _("Export KippoTaskStatus CSV")

    def get_latest_kippoprojectstatus_comment(self, obj):
        result = ""
        latest_status = obj.get_latest_kippoprojectstatus()
        if latest_status:
            result = latest_status.comment
            spaces = "&nbsp;" * 75
            result = format_html("{result}<br/>" + spaces, result=result)
        return result

    get_latest_kippoprojectstatus_comment.short_description = _("Latest Comment")

    def show_github_project_html_url(self, obj):
        url = ""
        if obj.github_project_html_url:
            url = format_html('<a href="{url}">{url}</a>', url=obj.github_project_html_url)
        return url

    show_github_project_html_url.short_description = _("GitHub Project URL")

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if instance._state.adding:  # Only for create (needed for handling uuid field as id)
                instance.created_by = request.user  # only update created_by once!
            instance.updated_by = request.user
            instance.save()
        formset.save_m2m()

    def get_form(self, request, obj=None, **kwargs):
        """Set defaults based on request user"""
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["project_manager"].initial = request.user.id

        user_initial_organization, user_organizations = get_user_session_organization(request)
        if not user_initial_organization:
            self.message_user(
                request, "User has not OrganizationMembership defined! Must belong to an Organization to create a project", level=messages.ERROR
            )
        form.base_fields["organization"].initial = user_initial_organization
        form.base_fields["organization"].queryset = request.user.memberships.all()
        return form

    def save_model(self, request, obj, form, change):
        if obj.pk is None:
            # expect only not not exist IF creating a new Project via ADMIN
            obj.created_by = request.user
            obj.updated_by = request.user
        else:
            obj.updated_by = request.user

        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations).order_by("organization").distinct()


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

    def get_project_name(self, obj):
        return obj.project.name

    get_project_name.short_description = _("Project")

    def get_task_count(self, obj) -> int:
        result = 0
        if obj:
            result = obj.kippotask_milestone.count()
        return result

    get_task_count.short_description = _("Task Count")

    def response_add(self, request, obj, post_url_continue=None):
        """Overridding Redirect to the KippoProject page after edit."""
        project_url = obj.project.get_admin_url()
        return HttpResponseRedirect(project_url)

    def response_change(self, request, obj):
        """Overriding Redirect to the KippoProject page after edit."""
        project_url = obj.project.get_admin_url()
        return HttpResponseRedirect(project_url)

    def get_queryset(self, request):
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


@admin.register(ProjectAssignment)
class ProjectAssignmentAdmin(UserCreatedBaseModelAdmin):
    list_display = ("project", "get_project_organization", "user")

    def get_project_organization(self, obj):
        organization_name = obj.project.organization.name
        return organization_name

    get_project_organization.short_description = _("Organization")


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


# allow additional admin to filtered ActiveKippoProject model
admin.site.register(ActiveKippoProject, KippoProjectAdmin)
