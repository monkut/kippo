import csv
import logging
from string import ascii_lowercase
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html
from django.http import HttpResponseRedirect, HttpResponse
from django.utils.translation import ugettext_lazy as _
from common.admin import UserCreatedBaseModelAdmin, AllowIsStaffAdminMixin
from ghorgs.managers import GithubOrganizationManager

from tasks.models import KippoTaskStatus

from .functions import collect_existing_github_projects, get_kippoproject_taskstatus_csv_rows
from .models import (
    KippoProject,
    ActiveKippoProject,
    KippoProjectStatus,
    KippoMilestone,
    ProjectColumnSet,
    ProjectColumn,
    ProjectAssignment,
    GithubMilestoneAlreadyExists,
)


logger = logging.getLogger(__name__)


class KippoMilestoneReadOnlyInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoMilestone
    extra = 0
    fields = (
        'title',
        'start_date',
        'target_date',
        'actual_date',
        'allocated_staff_days',
        'description',
    )
    readonly_fields = (
        'title',
        'start_date',
        'target_date',
        'actual_date',
        'allocated_staff_days',
        'description',
    )

    def has_add_permission(self, request, obj):  # No Add button
        return False

    def get_queryset(self, request):
        # order milestones as expected
        qs = super().get_queryset(request).order_by('target_date')
        return qs


class KippoMilestoneAdminInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoMilestone
    extra = 0
    fields = (
        'title',
        'start_date',
        'target_date',
        'actual_date',
        'allocated_staff_days',
        'description',
    )

    def get_queryset(self, request):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


class KippoProjectStatusReadOnlyInine(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoProjectStatus
    extra = 0
    fields = ('created_datetime', 'created_by', 'comment')
    readonly_fields = ('created_datetime', 'created_by', 'comment')

    def has_add_permission(self, request, obj):  # No Add button
        return False

    def get_queryset(self, request):
        # order milestones as expected
        qs = super().get_queryset(request).order_by('created_datetime')
        return qs


class KippoProjectStatusAdminInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoProjectStatus
    extra = 1
    fields = ('comment', )

    def get_queryset(self, request):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


def collect_existing_github_projects_action(modeladmin, request, queryset) -> None:
    """
    Admin Action to discover existing github projects and add to kippo as KippoProject objects
    """
    # get request user organization
    organization = request.user.organization
    added_projects = collect_existing_github_projects(organization)
    modeladmin.message_user(
        request,
        message=f'({len(added_projects)}) KippoProjects created from GitHub Organizational Projects',
        level=messages.INFO,
    )
collect_existing_github_projects_action.short_description = _('Collect Github Projects')  # noqa


def create_github_organizational_project_action(modeladmin, request, queryset) -> None:
    """
    Admin Action command to create a github organizational project from the selected KippoProject(s)

    Where an existing Github Organization project does not exist (not assigned)
    """
    successful_creation_projects = []
    skipping = []
    for kippo_project in queryset:
        if kippo_project.github_project_url:
            message = f'{kippo_project.name} already has GitHub Project set ({kippo_project.github_project_url}), SKIPPING!'
            logger.warning(message)
            skipping.append(message)
        else:
            if not kippo_project.columnset:
                modeladmin.message_user(
                    request,
                    message=f'ProjectColumnSet not defined for {kippo_project}, cannot create Github Project!',
                    level=messages.ERROR,
                )
                return

            columns = kippo_project.get_column_names()
            github_organization_name = kippo_project.organization.github_organization_name
            githubaccesstoken = kippo_project.organization.githubaccesstoken
            github_manager = GithubOrganizationManager(organization=github_organization_name,
                                                       token=githubaccesstoken.token)
            # create the organizational project in github
            # create_organizational_project(organization: str, name: str, description: str, columns: list=None) -> Tuple[str, List[object]]:
            url, _ = github_manager.create_organizational_project(
                name=kippo_project.github_project_name,
                description=kippo_project.github_project_description,
                columns=columns,
            )
            kippo_project.github_project_url = url
            kippo_project.save()
            successful_creation_projects.append((kippo_project.name, url, columns))
    if skipping:
        for m in skipping:
            modeladmin.message_user(
                request,
                message=m,
                level=messages.WARNING,
            )
    if successful_creation_projects:
        modeladmin.message_user(
            request,
            message=f'({len(successful_creation_projects)}) GitHub Projects Created: {successful_creation_projects}',
            level=messages.INFO,
        )
create_github_organizational_project_action.short_description = _('Create Github Organizational Project(s) for selected')  # noqa: E305


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
                        message=f'({kippo_project.name}) {created_octocat_milestone.repository.name} created milestone: '
                                f'{milestone.title} ({milestone.start_date} - {milestone.target_date})',
                        level=messages.INFO,
                    )
            except GithubMilestoneAlreadyExists as e:
                modeladmin.message_user(
                    request,
                    message=f'({kippo_project.name}) Failed to create milestone for related repository(ies): {e.args}',
                    level=messages.ERROR,
                )
create_github_repository_milestones_action.short_description = _(f'Create related Github Repository Milestone(s) for selected')  # noqa: E305


@admin.register(KippoProject)
class KippoProjectAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        'id',
        'name',
        'phase',
        'category',
        'get_confidence_display',
        'updated_by',
        'get_latest_kippoprojectstatus_comment',
        'start_date',
        'target_date',
        'show_github_project_url',
        'display_as_active',
        'updated_datetime',
    )
    list_display_links = (
        'id',
        'name',
    )
    search_fields = (
        'name',
        'phase',
        'category',
        'problem_definition',
    )
    ordering = (
        '-confidence',
        'phase',
    )
    actions = [
        create_github_organizational_project_action,
        create_github_repository_milestones_action,
        'export_project_kippotaskstatus_csv',
    ]
    inlines = [
        KippoMilestoneReadOnlyInline,
        KippoMilestoneAdminInline,
        KippoProjectStatusReadOnlyInine,
        KippoProjectStatusAdminInline,
    ]

    def get_confidence_display(self, obj):
        result = ''
        if obj.confidence:
            result = f'{obj.confidence} %'
        return result
    get_confidence_display.admin_order_field = 'confidence'
    get_confidence_display.short_description = 'confidence'

    def export_project_kippotaskstatus_csv(self, request, queryset):
        """Allow export to csv from admin"""
        if queryset.count() != 1:
            self.message_user(
                request,
                _('CSV Export action only supports single Project selection'),
                level=messages.ERROR
            )
        else:
            project = queryset[0]
            logger.debug(f'Generating KippoTaskStatus CSV for: {project.name}')
            project_slug = ''.join(c for c in project.name.replace(' ', '').lower() if c in ascii_lowercase)
            if not project_slug:
                project_slug = project.id
            filename = f'{project_slug}_{timezone.now().strftime("%Y%m%d_%H%M%Z")}.csv'
            logger.debug(f'filename: {filename}')
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename={filename}'
            writer = csv.writer(response)
            try:
                csv_row_generator = get_kippoproject_taskstatus_csv_rows(project, with_headers=True)
                writer.writerows(csv_row_generator)
                return response
            except KippoTaskStatus.DoesNotExist:
                self.message_user(
                    request,
                    _(f'No status entries exist for project: {project.name}'),
                    level=messages.WARNING
                )
    export_project_kippotaskstatus_csv.short_description = _('Export KippoTaskStatus CSV')

    def get_latest_kippoprojectstatus_comment(self, obj):
        result = ''
        latest_status = obj.get_latest_kippoprojectstatus()
        if latest_status:
            result = latest_status.comment
            spaces = '&nbsp;' * 75
            result = format_html('{result}<br/>' + spaces, result=result)
        return result
    get_latest_kippoprojectstatus_comment.short_description = _('Latest Comment')

    def show_github_project_url(self, obj):
        url = ''
        if obj.github_project_url:
            url = format_html('<a href="{url}">{url}</a>', url=obj.github_project_url)
        return url
    show_github_project_url.short_description = _('GitHub Project URL')

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if instance.id is None:
                instance.created_by = request.user  # only update created_by once!
            instance.updated_by = request.user
            instance.save()
        formset.save_m2m()

    def get_form(self, request, obj=None, **kwargs):
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['project_manager'].initial = request.user.id
        return form

    def save_model(self, request, obj, form, change):
        if obj.pk is None:
            # expect only not not exist IF creating a new Project via ADMIN
            obj.organization = request.user.organization

            obj.created_by = request.user
            obj.updated_by = request.user
        else:
            obj.updated_by = request.user

        super().save_model(request, obj, form, change)


@admin.register(KippoMilestone)
class KippoMilestoneAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        'title',
        'get_project_name',
        'is_completed',
        'start_date',
        'target_date',
        'actual_date',
        'updated_by',
        'updated_datetime',
    )
    search_fields = (
        'title',
        'description',
    )
    ordering = (
        'project',
        'target_date',
    )

    def get_project_name(self, obj):
        return obj.project.name
    get_project_name.short_description = _('Project')

    def response_add(self, request, obj, post_url_continue=None):
        """Overridding Redirect to the KippoProject page after edit.
        """
        project_url = obj.project.get_admin_url()
        return HttpResponseRedirect(project_url)

    def response_change(self, request, obj):
        """Overriding Redirect to the KippoProject page after edit.
        """
        project_url = obj.project.get_admin_url()
        return HttpResponseRedirect(project_url)


class ProjectColumnInline(admin.TabularInline):
    model = ProjectColumn
    extra = 3


@admin.register(ProjectColumnSet)
class ProjectColumnSetAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        'name',
        'get_column_names',
    )
    inlines = [ProjectColumnInline]


@admin.register(ProjectAssignment)
class ProjectAssignmentAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        'project',
        'get_project_organization',
        'user'
    )

    def get_project_organization(self, obj):
        organization_name = obj.project.organization.name
        return organization_name
    get_project_organization.short_description = _('Organization')


admin.site.register(ActiveKippoProject, KippoProjectAdmin)
