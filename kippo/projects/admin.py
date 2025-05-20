import csv
import logging
import urllib.parse
from collections import Counter, defaultdict
from collections.abc import Iterable
from string import ascii_lowercase

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from commons.definitions import SATURDAY
from commons.functions import get_current_month_date_range
from commons.widgets import MonthYearWidget
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Model
from django.forms import BaseFormSet, Form
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    request as DjangoRequest,  # noqa: N812
)
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from ghorgs.managers import GithubOrganizationManager
from rangefilter.filters import DateRangeFilterBuilder
from tasks.models import KippoTaskStatus
from tasks.periodic.tasks import collect_github_project_issues

from .exceptions import GithubMilestoneAlreadyExistsError
from .functions import (
    generate_kippoprojectusermonthlystatisfaction_csv,
    generate_kippoprojectuserstatisfactionresult_csv,
    generate_projectmonthlyeffort_csv,
    generate_projectstatuscomments_csv,
    generate_projectweeklyeffort_csv,
    get_kippoproject_taskstatus_csv_rows,
    get_user_session_organization,
)
from .models import (
    ActiveKippoProject,
    CollectIssuesAction,
    KippoMilestone,
    KippoProject,
    KippoProjectStatus,
    KippoProjectUserMonthlyStatisfactionResult,
    KippoProjectUserStatisfactionResult,
    ProjectAssignment,
    ProjectColumn,
    ProjectColumnSet,
    ProjectWeeklyEffort,
)

logger = logging.getLogger(__name__)


class KippoMilestoneReadOnlyInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoMilestone
    extra = 0
    fields = ("title", "start_date", "target_date", "actual_date", "allocated_staff_days", "description")
    readonly_fields = ("title", "start_date", "target_date", "actual_date", "allocated_staff_days", "description")
    classes = ["collapse"]

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None):  # No Add button
        return False

    def get_queryset(self, request: DjangoRequest):
        # order milestones as expected
        qs = super().get_queryset(request).order_by("target_date")
        return qs


class KippoMilestoneAdminInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoMilestone
    extra = 0
    fields = ("title", "start_date", "target_date", "actual_date", "allocated_staff_days", "description")
    classes = ["collapse"]

    def get_queryset(self, request: DjangoRequest):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


class ProjectWeeklyEffortReadOnlyInine(AllowIsStaffAdminMixin, admin.TabularInline):
    model = ProjectWeeklyEffort
    extra = 0
    fields = ("week_start", "user", "hours")
    readonly_fields = ("week_start", "user", "hours")
    classes = ["collapse"]

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None) -> bool:  # No Add button
        return False

    def get_queryset(self, request: DjangoRequest):
        # order milestones as expected
        three_weeks_ago = (timezone.now() - timezone.timedelta(days=21)).date()
        # filter output
        qs = super().get_queryset(request).filter(week_start__gte=three_weeks_ago).order_by("week_start")
        return qs


class ProjectWeeklyEffortAdminInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = ProjectWeeklyEffort
    extra = 1
    fields = ("week_start", "user", "hours")

    def get_queryset(self, request: DjangoRequest):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs

    def get_formset(self, request: HttpRequest, obj: ProjectWeeklyEffort | None = None, **kwargs):
        """Added to filter the user selection list so that only user's belonging to the project's organization will be listed"""
        formset = super().get_formset(request, obj, **kwargs)
        if obj:  # parent model
            # get users belonging to the organization this project belongs to
            formset.form.base_fields["user"].initial = request.user
            related_organization_user_ids = OrganizationMembership.objects.filter(organization=obj.organization).values_list("user__id", flat=True)
            formset.form.base_fields["user"].queryset = KippoUser.objects.filter(id__in=related_organization_user_ids).order_by(
                "last_name", "username"
            )

        return formset


class KippoProjectStatusReadOnlyInine(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoProjectStatus
    extra = 0
    fields = ("created_datetime", "created_by", "comment")
    readonly_fields = ("created_datetime", "created_by", "comment")
    classes = ["collapse"]

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None):  # No Add button
        return False

    def get_queryset(self, request: DjangoRequest):
        # order milestones as expected
        five_weeks_ago_days = 7 * 5
        five_weeks_ago = timezone.now() - timezone.timedelta(days=five_weeks_ago_days)
        qs = super().get_queryset(request).filter(created_datetime__gte=five_weeks_ago).order_by("created_datetime")
        return qs


class KippoProjectStatusAdminInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoProjectStatus
    extra = 1
    fields = ("comment",)

    def get_queryset(self, request: DjangoRequest):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


def create_github_organizational_project_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:  # noqa: PLR0912
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
                    request,
                    message=f"ProjectColumnSet not defined for {kippo_project}, cannot create Github Project!",
                    level=messages.ERROR,
                )
                return

            columns = kippo_project.get_column_names()
            github_organization_name = kippo_project.organization.github_organization_name
            githubaccesstoken = kippo_project.organization.githubaccesstoken
            github_manager = GithubOrganizationManager(organization=github_organization_name, token=githubaccesstoken.token)
            # create the organizational project in github
            # create_organizational_project(organization: str, name: str, description: str, columns: list=None) -> Tuple[str, List[object]]:
            url, responses = github_manager.create_organizational_project(
                name=kippo_project.github_project_name,
                description=kippo_project.github_project_description,
                columns=columns,
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
                        github_project_id = item["data"]["createProject"]["project"]["databaseId"]
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
            request,
            message=f"({len(successful_creation_projects)}) GitHub Projects Created: {successful_creation_projects}",
            level=messages.INFO,
        )


create_github_organizational_project_action.short_description = _("Create Github Organizational Project(s) for selected")  # noqa: E305


def create_github_repository_milestones_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:
    """
    Admin Action command to create a github repository milestones for ALL
    repositories linked to the selected KippoProject(s).
    """
    for kippo_project in queryset:
        milestones = kippo_project.active_milestones()
        for milestone in milestones:
            try:
                created_octocat_milestones = milestone.update_github_milestones(request.user)
                for _, created_octocat_milestone in created_octocat_milestones:
                    modeladmin.message_user(
                        request,
                        message=f"({kippo_project.name}) {created_octocat_milestone.repository.name} created milestone: "
                        f"{milestone.title} ({milestone.start_date} - {milestone.target_date})",
                        level=messages.INFO,
                    )
            except GithubMilestoneAlreadyExistsError as e:
                modeladmin.message_user(
                    request,
                    message=f"({kippo_project.name}) Failed to create milestone for related repository(ies): {e.args}",
                    level=messages.ERROR,
                )


create_github_repository_milestones_action.short_description = _("Create related Github Repository Milestone(s) for selected")  # noqa: E305


def collect_project_github_repositories_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:
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
        request,
        message=f"({len(github_project_html_urls_to_update)}) KippoProjects updated from GitHub Organizational Projects",
        level=messages.INFO,
    )


collect_project_github_repositories_action.short_description = _("Collect Project Repositories")  # noqa: E305


@admin.register(KippoProject)
class KippoProjectAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        "id",
        "name",
        "phase",
        "category",
        "get_confidence_display",
        "get_projecteffort_display",
        "get_latest_kippoprojectstatus_comment",
        "start_date",
        "target_date",
        "get_kippoprojectuserstatisfactionresult_usernames",
        "get_projectsurvey_display_url",
        "show_github_project_html_url",
        "display_as_active",
        "get_updated_by_display",
        "updated_datetime",
    )
    list_display_links = ("id", "name")
    search_fields = ("id", "name", "phase", "category", "problem_definition")
    ordering = ("organization", "-display_as_active", "-confidence", "phase", "name")
    actions = [
        create_github_organizational_project_action,
        create_github_repository_milestones_action,
        collect_project_github_repositories_action,
        "export_project_kippotaskstatus_csv",
        "export_kippoprojectstatus_comments_csv",
    ]
    inlines = [
        # Milestones not used atm, commenting out.
        # KippoMilestoneReadOnlyInline,
        # KippoMilestoneAdminInline,
        ProjectWeeklyEffortReadOnlyInine,
        KippoProjectStatusReadOnlyInine,
        ProjectWeeklyEffortAdminInline,
        KippoProjectStatusAdminInline,
    ]

    def has_add_permission(self, request: DjangoRequest, obj: KippoProject | None = None):  # No Add button
        # check if user has organization memberships
        # - if not can't add new projects
        return request.user.memberships.exists()

    def get_updated_by_display(self, obj: KippoProject) -> str:
        result = ""
        if obj:
            result = obj.updated_by.username
        return result

    get_updated_by_display.short_description = "updated by"

    def get_confidence_display(self, obj: KippoProject):
        result = ""
        if obj.confidence:
            result = f"{obj.confidence} %"
        return result

    get_confidence_display.admin_order_field = "confidence"
    get_confidence_display.short_description = "confidence"

    def get_kippoprojectuserstatisfactionresult_usernames(self, obj: KippoProject | None = None) -> str:
        result = ""
        if obj:
            result = format_html(
                "<br>".join(
                    KippoProjectUserStatisfactionResult.objects.filter(project=obj)
                    .order_by("created_by__username")
                    .values_list("created_by__username", flat=True)
                )
            )
        return result

    get_kippoprojectuserstatisfactionresult_usernames.short_description = f"{KippoProjectUserStatisfactionResult._meta.verbose_name} Submitted Users"

    def get_projectsurvey_display_url(self, obj: KippoProject) -> str:
        url = obj.get_projectsurvey_url()
        html_encoded_url = ""
        if url:
            html_encoded_url = format_html(f"<a href='{url}'>Survey URL</a>")
        return html_encoded_url

    get_projectsurvey_display_url.short_description = _("Project Survey URL")

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
        filename = f"{project_slug}_{timezone.now().strftime('%Y%m%d_%H%M%Z')}.csv"
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
            now = timezone.now()
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

    def get_latest_kippoprojectstatus_comment(self, obj: KippoProject):
        result = ""
        latest_status = obj.get_latest_kippoprojectstatus()
        if latest_status:
            display_date = latest_status.created_datetime.strftime("(%m/%d) ")
            result = latest_status.comment
            spaces = "&nbsp;" * 75
            result = format_html("{display_date}{result}<br/>" + spaces, display_date=display_date, result=result)
        return result

    get_latest_kippoprojectstatus_comment.short_description = _("Latest Comment")

    @admin.display(description=_("Effort Hours"))
    def get_projecteffort_display(self, obj: KippoProject | None = None) -> str:
        result = "-"
        if obj:
            result = obj.get_projecteffort_display()
        return result

    def show_github_project_html_url(self, obj: KippoProject) -> str:
        url = ""
        if obj.github_project_html_url:
            url = format_html('<a href="{url}">{url}</a>', url=obj.github_project_html_url)
        return url

    show_github_project_html_url.short_description = _("GitHub Project URL")

    def save_formset(self, request: DjangoRequest, form: Form, formset: BaseFormSet, change: bool) -> None:
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if instance._state.adding:  # Only for create (needed for handling uuid field as id)
                instance.created_by = request.user  # only update created_by once!
            instance.updated_by = request.user
            instance.save()
        formset.save_m2m()

    def get_form(self, request: DjangoRequest, obj: KippoProject | None = None, **kwargs) -> Form:
        """Set defaults based on request user"""
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)
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

        # remove add/change/delete buttons from all ForeignKey fields
        for fieldname in form.base_fields:
            form.base_fields[fieldname].widget.can_add_related = False
            form.base_fields[fieldname].widget.can_change_related = False
            form.base_fields[fieldname].widget.can_delete_related = False

        return form

    def save_model(self, request: DjangoRequest, obj: KippoProject, form: Form, change: bool):
        if obj.pk is None:
            # expect only not not exist IF creating a new Project via ADMIN
            obj.created_by = request.user
            obj.updated_by = request.user
        else:
            obj.updated_by = request.user

        super().save_model(request, obj, form, change)

    def get_queryset(self, request: DjangoRequest):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations).order_by("organization").distinct()


@admin.register(ActiveKippoProject)
class ActiveKippoProjectAdmin(KippoProjectAdmin):
    list_display = (
        "id",
        "name",
        "phase",
        "get_confidence_display",
        "get_projecteffort_display",
        "get_latest_kippoprojectstatus_comment",
        "start_date",
        "target_date",
        "get_kippoprojectuserstatisfactionresult_usernames",
        "get_projectsurvey_display_url",
        "show_github_project_html_url",
        "get_updated_by_display",
        "updated_datetime",
    )

    fieldsets = [
        (
            None,
            {
                "fields": (
                    "name",
                    "confidence",
                    "project_manager",
                )
            },
        ),
        (
            _("Dates & Estimates"),
            {
                "classes": ("collapse",),
                "fields": (
                    "start_date",
                    "target_date",
                    "actual_date",
                    "allocated_staff_days",
                ),
            },
        ),
        (
            _("Details"),
            {
                "classes": ("collapse",),
                "fields": (
                    "organization",
                    "phase",
                    "category",
                    "slack_channel_name",
                    "columnset",
                    "is_closed",
                    "display_as_active",
                    "display_in_project_report",
                    "document_url",
                    "github_project_html_url",
                    "github_project_api_url",
                    "problem_definition",
                ),
            },
        ),
    ]

    def has_delete_permission(self, request: DjangoRequest, obj: Model | None = None):
        """Remove delete button from details/change page"""
        if "/change/" in request.path:
            return False
        return super().has_delete_permission(request, obj)


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
        return obj.project.name

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


@admin.register(ProjectAssignment)
class ProjectAssignmentAdmin(UserCreatedBaseModelAdmin):
    list_display = ("project", "get_project_organization", "user")

    def get_project_organization(self, obj: ProjectAssignment):
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


@admin.register(ProjectWeeklyEffort)
class ProjectWeeklyEffortAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = ("get_project_name", "week_start", "get_user_display_name", "hours")
    ordering = ("project", "-week_start", "user")
    search_fields = (
        "project__name",
        "user__last_name",
    )
    actions = ("download_csv", "download_monthly_csv")

    def get_list_filter(self, request: DjangoRequest) -> list:
        current_month_start, current_month_end = get_current_month_date_range()
        return [
            ("week_start", DateRangeFilterBuilder(title="date filter", default_start=current_month_start, default_end=current_month_end)),
        ]

    def get_project_name(self, obj: ProjectWeeklyEffort | None = None) -> str:
        result = "-"
        if obj and obj.project and obj.project.name:
            result = obj.project.name
        return result

    get_project_name.short_description = _("Project")

    def get_user_display_name(self, obj: ProjectWeeklyEffort | None = None) -> str:
        result = "-"
        if obj:
            result = obj.user.display_name
        return result

    get_user_display_name.short_description = _("user")

    @admin.action(description=_("Download ProjectWeeklyEffort CSV"))
    def download_csv(self, request: DjangoRequest, queryset: models.QuerySet) -> HttpResponseRedirect | None:
        if not ProjectWeeklyEffort.objects.filter(project__organization__in=request.user.organizations).exists():
            self.message_user(request, _("No ProjectWeeklyEffort exists!"), level=messages.WARNING)
            return None
        # initiate creation
        now = timezone.now()
        filename = now.strftime("project-effort-%Y%m%d%H%M%S.csv")
        key = f"tmp/download/{filename}"
        selected_query_id = list(queryset.values_list("id", flat=True))
        generate_projectweeklyeffort_csv(user_id=str(request.user.pk), key=key, effort_ids=selected_query_id)
        # redirect to waiter
        urlencoded_key = urllib.parse.quote_plus(key)
        download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}"
        return HttpResponseRedirect(redirect_to=download_waiter_url)

    @admin.action(description="Download ProjectMonthlyEffort CSV")
    def download_monthly_csv(self, request: DjangoRequest, queryset: models.QuerySet):
        if not ProjectWeeklyEffort.objects.filter(project__organization__in=request.user.organizations).exists():
            self.message_user(request, _("No ProjectWeeklyEffort exists!"), level=messages.WARNING)
            return None
        # initiate creation
        now = timezone.localtime()
        filename = now.strftime("project-monthly-effort-%Y%m%d%H%M%S.csv")
        key = f"tmp/download/{filename}"
        selected_query_id = list(queryset.values_list("id", flat=True))
        generate_projectmonthlyeffort_csv(user_id=str(request.user.pk), key=key, effort_ids=selected_query_id)
        # redirect to waiter
        urlencoded_key = urllib.parse.quote_plus(key)
        download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}"
        return HttpResponseRedirect(redirect_to=download_waiter_url)

    def get_form(self, request: DjangoRequest, obj: ProjectWeeklyEffort | None = None, **kwargs):
        """Set defaults based on request user"""
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["user"].initial = request.user.id
        form.base_fields["user"].widget = forms.HiddenInput()
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
        user_projects = KippoProject.objects.filter(organization__in=user_memberships)
        form.base_fields["project"].initial = user_projects.first()
        form.base_fields["project"].queryset = user_projects
        return form

    def get_queryset(self, request: DjangoRequest) -> models.QuerySet:
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(project__organization__in=request.user.organizations).order_by("project__organization")

    def get_fiscal_year_org_per_user_weeklyeffort(self, organizations: Iterable[KippoOrganization]) -> tuple:  # noqa: C901, PLR0912
        from accounts.models import PublicHoliday

        all_months = set()
        monthly_expected_hours = Counter()

        monthly_week_starts = []
        results = {}
        now = timezone.now()
        monthly_expected_hours_processed = False
        for org in organizations:
            user_weekstarts = defaultdict(list)
            results[org.name] = {}
            if now.month < org.fiscalyear_start_month:
                current_fiscal_year = timezone.datetime(now.year - 1, org.fiscalyear_start_month, 1, tzinfo=timezone.timezone.utc)
            else:
                current_fiscal_year = timezone.datetime(now.year, org.fiscalyear_start_month, 1, tzinfo=timezone.timezone.utc)
            # get organization users
            # -- aggregate projectweeklyeffort per user per month
            users = org.get_membership_kippousers()
            projectweeklyeffort = ProjectWeeklyEffort.objects.filter(
                user__in=users, week_start__gte=current_fiscal_year.date(), project__organization=org
            )
            sum_index = 0
            flag_index = 1
            for effort in projectweeklyeffort:
                if effort.user.username not in results[org.name]:
                    results[org.name][effort.user.username] = {}
                if effort.week_start.month not in results[org.name][effort.user.username]:
                    results[org.name][effort.user.username][effort.week_start.month] = [0, False]
                results[org.name][effort.user.username][effort.week_start.month][sum_index] += effort.hours
                user_weekstarts[effort.user.username].append(effort.week_start)

            # remove public holidays from total
            # -- calculate total workdays from fiscal start
            if not monthly_expected_hours:
                current = current_fiscal_year
                while current <= now:
                    all_months.add(current.month)
                    if current.weekday() < SATURDAY:  # SAT=5, SUN=6
                        monthly_expected_hours[current.month] += 1
                    if current.weekday() == 0:
                        monthly_week_starts.append(current.date())
                    current += timezone.timedelta(days=1)
            # apply hours
            for month in monthly_expected_hours:
                if not monthly_expected_hours_processed:
                    monthly_expected_hours[month] *= org.day_workhours
                # -- update user dictionaries with 0s
                for org_user_info in results.values():
                    for user_month_data in org_user_info.values():
                        if month and month not in user_month_data:
                            user_month_data[month] = [0, False]
                        elif (
                            user_month_data[month][sum_index]
                            > monthly_expected_hours[month] + monthly_expected_hours[month] * settings.PROJECT_EFFORT_EXCEED_PERCENTAGE
                        ):
                            user_month_data[month][flag_index] = True
            monthly_expected_hours_processed = True
            # re-sort user_data
            for org_key in results:  # noqa: PLC0206
                for user_key in results[org_key].keys():  # noqa: PLC0206
                    if "missing" not in results[org_key][user_key]:
                        this_week_start = now
                        while this_week_start.weekday() != 0:
                            this_week_start -= timezone.timedelta(days=1)

                        results[org_key][user_key] = dict(sorted(results[org_key][user_key].items()))
                        # add missing
                        user_missing_weekstarts = set(monthly_week_starts) - set(user_weekstarts[user_key])
                        results[org_key][user_key]["missing"] = [
                            ", ".join(d.strftime("%m-%d") for d in sorted(user_missing_weekstarts) if d != this_week_start.date()),
                            False,
                        ]  # noqa: PLC0206

        # -- calculate public holidays
        for holiday in PublicHoliday.objects.filter(day__gte=current_fiscal_year.date(), day__lte=now):
            # -- subtract public holidays from current total
            monthly_expected_hours[holiday.day.month] -= 1 * org.day_workhours

        return dict(results), dict(monthly_expected_hours), tuple(all_months)

    def changelist_view(self, request: DjangoRequest, extra_context: dict | None = None):
        original_response = super().changelist_view(request, extra_context)
        organizations = request.user.organizations
        summary_results, expected_hours, all_months = self.get_fiscal_year_org_per_user_weeklyeffort(organizations)

        extra_context = dict(
            self.admin_site.each_context(request),
            summary=summary_results,
            expected=expected_hours,
            months=all_months,
            monthly_exceed_percentage=int(settings.PROJECT_EFFORT_EXCEED_PERCENTAGE * 100),
        )
        if hasattr(original_response, "context_data") and original_response.context_data:
            extra_context.update(original_response.context_data)
        elif isinstance(original_response, HttpResponseRedirect):
            return original_response
        return TemplateResponse(request, "admin/projects/weeklyeffortadmin.html", extra_context)


@admin.register(KippoProjectUserStatisfactionResult)
class KippoProjectUserStatisfactionResultAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        "get_project_name",
        "get_project_targetdate",
        "get_user_display_name",
    )
    ordering = (
        "project",
        "-project__target_date",
        "created_datetime",
    )
    actions = ("download_csv",)

    def get_project_name(self, obj: KippoProjectUserStatisfactionResult | None = None) -> str:
        result = "-"
        if obj and obj.project and obj.project.name:
            result = obj.project.name
        return result

    get_project_name.short_description = _("Project")

    def get_user_display_name(self, obj: KippoProjectUserStatisfactionResult | None = None) -> str:
        result = "-"
        if obj:
            result = obj.created_by.display_name
        return result

    get_user_display_name.short_description = _("User")

    def get_project_targetdate(self, obj: KippoProjectUserStatisfactionResult | None = None) -> str:
        result = "-"
        if obj:
            result = str(obj.project.target_date)
        return result

    get_project_targetdate.short_description = _("プロジェクト目標完了日")

    def get_form(self, request: DjangoRequest, obj: KippoProjectUserStatisfactionResult | None = None, **kwargs):
        """Filter to use only opened projects"""
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)

        def get_project_display_name(project: KippoProject) -> str:
            return project.name

        if "project" in form.base_fields:
            user_organizations = request.user.organizations
            open_projects = (
                KippoProject.objects.filter(is_closed=False, organization__in=user_organizations).exclude(phase="anon-project").order_by("name")
            )
            form.base_fields["project"].initial = open_projects.first()
            form.base_fields["project"].queryset = open_projects
            form.base_fields["project"].label_from_instance = get_project_display_name
        return form

    def has_change_permission(self, request: DjangoRequest, obj: KippoProjectUserStatisfactionResult | None = None) -> bool:
        has_permission = False
        if request.user.is_superuser or obj and request.user == obj.created_by:
            has_permission = True
        return has_permission

    def has_delete_permission(self, request: DjangoRequest, obj: KippoProjectUserStatisfactionResult | None = None) -> bool:
        return self.has_change_permission(request, obj)

    def download_csv(self, request: DjangoRequest, queryset: models.QuerySet) -> HttpResponseRedirect | None:
        if not KippoProjectUserStatisfactionResult.objects.filter(project__organization__in=request.user.organizations).exists():
            self.message_user(
                request,
                _("Does Not Exist: %s") % KippoProjectUserStatisfactionResult._meta.verbose_name,
                level=messages.WARNING,
            )
            return None
        self.message_user(request, _("Preparing CSV..."), level=messages.INFO)
        # initiate creation
        now = timezone.now()
        filename = now.strftime("project-userstatisfactionresult-%Y%m%d%H%M%S.csv")
        key = f"tmp/download/{filename}"
        organization_pks = [str(org.pk) for org in request.user.organizations]
        generate_kippoprojectuserstatisfactionresult_csv(organization_pks=organization_pks, key=key)
        # redirect to waiter
        urlencoded_key = urllib.parse.quote_plus(key)
        download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}"
        return HttpResponseRedirect(redirect_to=download_waiter_url)

    download_csv.short_description = _("Download %s CSV") % KippoProjectUserStatisfactionResult._meta.verbose_name


class KippoProjectUserMonthlyStatisfactionResultAdminForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()
        submitted_date = cleaned_data["date"]
        existing_obj = KippoProjectUserMonthlyStatisfactionResult.objects.filter(
            project=cleaned_data["project"],
            created_by=self.request.user,
            date__year=submitted_date.year,
            date__month=submitted_date.month,
        ).exists()
        if existing_obj:
            raise ValidationError(
                f"Entry Already exists: {cleaned_data['project'].name} {self.request.user.display_name} {submitted_date.year}-{submitted_date.month}"
            )
        return cleaned_data


@admin.register(KippoProjectUserMonthlyStatisfactionResult)
class KippoProjectUserMonthlyStatisfactionResultAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        "get_project_name",
        "get_project_targetdate",
        "get_entry_yearmonth",
        "get_user_display_name",
    )
    ordering = ("project", "-project__target_date", "created_by", "created_datetime")
    actions = ("download_csv",)
    form = KippoProjectUserMonthlyStatisfactionResultAdminForm
    formfield_overrides = {
        models.DateField: {"widget": MonthYearWidget},
    }

    def get_project_name(self, obj: KippoProjectUserMonthlyStatisfactionResult | None = None) -> str:
        result = "-"
        if obj and obj.project and obj.project.name:
            result = obj.project.name
        return result

    get_project_name.short_description = _("Project")

    def get_user_display_name(self, obj: KippoProjectUserMonthlyStatisfactionResult | None = None) -> str:
        result = "-"
        if obj:
            result = obj.created_by.display_name
        return result

    get_user_display_name.short_description = _("User")

    def get_project_targetdate(self, obj: KippoProjectUserMonthlyStatisfactionResult | None = None) -> str:
        result = "-"
        if obj:
            result = str(obj.project.target_date)
        return result

    get_project_targetdate.short_description = _("プロジェクト目標完了日")

    def get_entry_yearmonth(self, obj: KippoProjectUserMonthlyStatisfactionResult | None = None) -> str:
        result = "-"
        if obj:
            result = obj.date.strftime("%Y-%m")
        return result

    get_entry_yearmonth.short_description = _("月")

    def get_form(self, request: DjangoRequest, obj: KippoProjectUserMonthlyStatisfactionResult | None = None, **kwargs):
        """Filter to use only opened projects"""
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)
        form.request = request

        def get_project_display_name(project: KippoProject) -> str:
            return project.name

        if "project" in form.base_fields:
            user_organizations = request.user.organizations
            open_projects = KippoProject.objects.filter(is_closed=False, organization__in=user_organizations, phase="anon-project").order_by("name")
            form.base_fields["project"].initial = open_projects.first()
            form.base_fields["project"].queryset = open_projects
            form.base_fields["project"].label_from_instance = get_project_display_name
        return form

    def save_model(self, request: DjangoRequest, obj: KippoProjectUserMonthlyStatisfactionResult, form: Form, change: bool):
        obj.date = form.cleaned_data["date"]
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request: DjangoRequest, obj: KippoProjectUserMonthlyStatisfactionResult | None = None) -> bool:
        has_permission = False
        if request.user.is_superuser or obj and request.user == obj.created_by:
            has_permission = True
        return has_permission

    def has_delete_permission(self, request: DjangoRequest, obj: KippoProjectUserMonthlyStatisfactionResult | None = None) -> bool:
        return self.has_change_permission(request, obj)

    def download_csv(self, request: HttpRequest, queryset: models.QuerySet) -> HttpResponseRedirect | None:
        if not KippoProjectUserMonthlyStatisfactionResult.objects.filter(project__organization__in=request.user.organizations).exists():
            self.message_user(
                request,
                _("No %s exists!") % KippoProjectUserMonthlyStatisfactionResult._meta.verbose_name,
                level=messages.WARNING,
            )
            return None
        self.message_user(request, _("Preparing CSV..."), level=messages.INFO)
        # initiate creation
        now = timezone.now()
        filename = now.strftime("project-monthlystatisfaction-%Y%m%d%H%M%S.csv")
        key = f"tmp/download/{filename}"
        organization_pks = [str(org.pk) for org in request.user.organizations]
        generate_kippoprojectusermonthlystatisfaction_csv(organization_pks=organization_pks, key=key)
        # redirect to waiter
        urlencoded_key = urllib.parse.quote_plus(key)
        download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}"
        return HttpResponseRedirect(redirect_to=download_waiter_url)

    download_csv.short_description = _("Download %s CSV") % KippoProjectUserMonthlyStatisfactionResult._meta.verbose_name
