from commons.admin import (
    AllowIsStaffAdminMixin,
    AllowIsStaffReadonlyMixin,
    OrganizationQuerysetModelAdminMixin,
    UserCreatedBaseModelAdmin,
)
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.db.models import QuerySet
from django.forms import Form
from django.http import request as DjangoRequest  # noqa: N812
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from octocat.models import GithubAccessToken
from projects.functions import collect_existing_github_projects
from projects.models import CollectIssuesAction
from social_django.models import Association, Nonce, UserSocialAuth
from tasks.periodic.tasks import collect_github_project_issues

from .models import (
    Country,
    EmailDomain,
    KippoOrganization,
    KippoUser,
    OrganizationInvite,
    OrganizationMembership,
    PersonalHoliday,
    PublicHoliday,
)


class EmailDomainAdminReadOnlyInline(admin.TabularInline):
    model = EmailDomain
    extra = 0
    fields = ("domain", "is_staff_domain", "updated_by", "updated_datetime", "created_by", "created_datetime")
    readonly_fields = ("domain", "is_staff_domain", "updated_by", "updated_datetime", "created_by", "created_datetime")

    def has_add_permission(self, request: DjangoRequest, obj: KippoOrganization):  # so that 'add button' is not available in admin
        return False

    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        # update so that Milestones are displayed in expected delivery order
        qs = super().get_queryset(request).order_by("created_datetime")
        return qs


class EmailDomainAdminInline(admin.TabularInline):
    model = EmailDomain
    extra = 0
    fields = ("domain", "is_staff_domain")

    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


class GithubAccessTokenAdminReadOnlyInline(admin.StackedInline):
    model = GithubAccessToken
    exclude = ("token",)
    fields = ("created_by", "created_datetime")
    readonly_fields = ("created_by", "created_datetime")

    def has_add_permission(self, request: DjangoRequest, obj: KippoOrganization) -> bool:
        return False


class GithubAccessTokenAdminInline(admin.StackedInline):
    model = GithubAccessToken
    extra = 0

    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs

    def has_add_permission(self, request: DjangoRequest, obj: KippoOrganization) -> bool:
        return True


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(AllowIsStaffReadonlyMixin, UserCreatedBaseModelAdmin):
    list_display = (
        "organization",
        "user",
        "get_user_github_login",
        "slack_username",
        "committed_days",
        "is_project_manager",
        "is_developer",
    )
    ordering = ("organization", "user")
    search_fields = ["user__username", "user__github_login", "slack_username"]

    def get_queryset(self, request: DjangoRequest):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations)

    def get_user_github_login(self, obj: OrganizationMembership) -> str:
        return obj.user.github_login

    get_user_github_login.short_description = _("Github Login")


@admin.register(OrganizationInvite)
class OrganizationInviteAdmin(AllowIsStaffReadonlyMixin, UserCreatedBaseModelAdmin):
    list_display = ("organization", "email", "expiration_date", "is_complete", "expiration_date", "processed_datetime")
    ordering = ("organization", "email")
    search_fields = ["email"]

    def get_queryset(self, request: DjangoRequest):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations)


@admin.register(KippoOrganization)
class KippoOrganizationAdmin(AllowIsStaffReadonlyMixin, OrganizationQuerysetModelAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        "name",
        "id",
        "github_organization_name",
        "default_task_category",
        "google_forms_project_survey_url",
        "github_webhook_secret",
        "github_webhook_url",
        "slack_webhook_url",
        "updated_by",
        "updated_datetime",
        "created_by",
        "created_datetime",
    )
    search_fields = ("name",)
    inlines = (
        GithubAccessTokenAdminReadOnlyInline,
        GithubAccessTokenAdminInline,
        EmailDomainAdminReadOnlyInline,
        EmailDomainAdminInline,
    )
    actions = ["collect_organization_projects_action", "collect_github_project_issues_action"]

    def get_form(self, request: DjangoRequest, obj: KippoOrganization | None = None, change: bool = False, **kwargs):
        form = super().get_form(request, obj, change, **kwargs)
        form.base_fields["slack_signing_secret"].widget = forms.PasswordInput()  # hide slack_signing_secret
        return form

    def collect_organization_projects_action(self, request: DjangoRequest, queryset: QuerySet) -> None:
        for organization in queryset:
            added_projects = collect_existing_github_projects(organization=organization, as_user=request.user)

            projects_string = ", ".join(p.name for p in added_projects)
            msg = f"Added [{organization.name}] ({len(added_projects)}) {projects_string}"
            self.message_user(request, msg, level=messages.INFO)

    collect_organization_projects_action.short_description = _("Collect Organization Project(s)")

    def collect_github_project_issues_action(self, request: DjangoRequest, queryset: QuerySet) -> None:
        status_effort_date = timezone.now().isoformat()
        for organization in queryset:
            action_tracker = CollectIssuesAction(organization=organization, created_by=request.user, updated_by=request.user)
            action_tracker.save()
            collect_github_project_issues(
                action_tracker_id=action_tracker.id,
                kippo_organization_id=str(organization.id),
                status_effort_date_iso8601=status_effort_date,
            )
            self.message_user(request, f"Processing Request: CollectIssuesAction(id={action_tracker.id})", level=messages.INFO)

    collect_github_project_issues_action.short_description = _("Collect Organization Project Issues")


@admin.register(KippoUser)
class KippoUserAdmin(AllowIsStaffReadonlyMixin, OrganizationQuerysetModelAdminMixin, UserAdmin):
    list_display = (
        "username",
        "id",
        "github_login",
        "get_github_organizations",
        "last_name",
        "first_name",
        "holiday_country",
        "date_joined",
        "last_login",
        "is_github_outside_collaborator",
        "is_staff",
        "is_superuser",
    )
    # limit displayed fields
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "github_login", "email", "holiday_country")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser")}),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )

    def get_is_collaborator(self, obj: KippoUser) -> bool:
        return obj.is_github_outside_collaborator

    get_is_collaborator.short_description = _("Is Collaborator")

    def get_github_organizations(self, obj: KippoUser) -> str:
        membership_organizations = []
        for organization in obj.memberships.all():
            name = organization.github_organization_name
            membership_organizations.append(name)
        return ", ".join(membership_organizations)

    get_github_organizations.short_description = _("Github Organizations")


@admin.register(PersonalHoliday)
class PersonalHolidayAdmin(AllowIsStaffAdminMixin, admin.ModelAdmin):
    list_display = ("user", "is_half", "day", "duration")

    def get_form(self, request: DjangoRequest, obj: PersonalHoliday | None = None, **kwargs) -> Form:
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            form.base_fields["user"].widget = forms.HiddenInput()
            form.base_fields["user"].initial = request.user
        return form

    def save_model(self, request: DjangoRequest, obj: PersonalHoliday, form: Form, change: bool) -> None:
        if not request.user.is_superuser:
            obj.user = request.user
        obj.save()

    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if not request.user.organizations:
            return qs.filter(user=request.user)
        return qs.filter(user__organizationmembership__organization__in=request.user.organizations).distinct()


@admin.register(Country)
class CountryAdmin(AllowIsStaffReadonlyMixin, admin.ModelAdmin):
    list_display = ("name", "alpha_2", "alpha_3", "country_code", "region")


@admin.register(PublicHoliday)
class PublicHolidayAdmin(AllowIsStaffReadonlyMixin, admin.ModelAdmin):
    list_display = ("name", "country", "day")


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    date_hierarchy = "action_time"
    readonly_fields = [field.name for field in LogEntry._meta.get_fields()]
    list_filter = ["user", "content_type"]
    search_fields = ["object_repr", "change_message"]
    list_display = ["__str__", "content_type", "action_time", "user", "object_link"]

    def has_add_permission(self, request: DjangoRequest) -> bool:
        return False

    def has_change_permission(self, request: DjangoRequest, obj: LogEntry | None = None) -> bool:
        return False

    def has_delete_permission(self, request: DjangoRequest, obj: LogEntry | None = None) -> bool:
        return False

    def has_view_permission(self, request: DjangoRequest, obj: LogEntry | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"

    def object_link(self, obj: LogEntry) -> str:
        if obj.action_flag == DELETION:
            link = obj.object_repr
        else:
            ct = obj.content_type
            obj_url = reverse(f"admin:{ct.app_label}_{ct.model}_change", args=[obj.object_id])
            display_name = escape(obj.object_repr)
            try:
                link = mark_safe(  # noqa: S308
                    f'<a href="{obj_url}">{display_name}</a>'
                )
            except NoReverseMatch:
                link = obj.object_repr
        return link

    object_link.admin_order_field = "object_repr"
    object_link.short_description = "object"

    def queryset(self, request: DjangoRequest) -> QuerySet:
        return super().queryset(request).prefetch_related("content_type")


admin.site.unregister(UserSocialAuth)
admin.site.unregister(Nonce)
admin.site.unregister(Association)
admin.site.unregister(Group)
