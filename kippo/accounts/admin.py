import logging

from commons.admin import (
    AllowIsStaffAdminMixin,
    AllowIsStaffReadonlyMixin,
    AllowIsSuperuserAdminMixin,
    OrganizationQuerysetModelAdminMixin,
    UserCreatedBaseModelAdmin,
)
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db.models import Model, Q, QuerySet
from django.forms import Form
from django.http import request as DjangoRequest  # noqa: N812
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from ghorgs.exceptions import GithubGraphQLError
from octocat.functions import get_organization_projects_v2
from octocat.models import GithubAccessToken
from projects.functions import collect_existing_github_projects
from projects.models import CollectIssuesAction, ProjectColumnSet
from social_django.models import Association, Nonce, UserSocialAuth
from tasks.periodic.tasks import collect_github_project_issues

from .definitions import KIPPOUSER_AUTOCOMPLETE_ORGANIZATION_PARAM
from .models import (
    AttendanceRecord,
    Country,
    EmailDomain,
    KippoOrganization,
    KippoUser,
    OrganizationInvite,
    OrganizationMembership,
    PersonalHoliday,
    PublicHoliday,
    SlackCommand,
)

logger = logging.getLogger(__name__)


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
    """Memberships are readable and writable only by superusers and organization admins, scoped to the organizations they administer.

    `is_admin` lives on this model, so scoping on plain membership would let an admin of one
    organization grant themselves the role in a second organization they merely belong to.
    Every gate here therefore reads `KippoUser.admin_organizations`, matching
    `OrganizationInviteAdmin`. Deletion stays superuser-only via `AllowIsStaffReadonlyMixin`.
    See kiconiaworks/kippo#57.
    """

    list_display = (
        "organization",
        "user",
        "get_user_github_login",
        "slack_username",
        "committed_days",
        "is_project_manager",
        "is_developer",
        "is_admin",
    )
    ordering = ("organization", "user")
    search_fields = ["user__username", "user__github_login", "slack_username"]

    @staticmethod
    def _is_any_organization_admin(user: KippoUser) -> bool:
        if not user.is_active or user.is_anonymous:
            return False
        return user.is_superuser or user.admin_organizations.exists()

    def _may_write(self, request: DjangoRequest, obj: OrganizationMembership | None = None) -> bool:
        user = request.user
        if not user.is_active or user.is_anonymous:
            return False
        if user.is_superuser:
            return True
        if obj is None:
            # changelist/add-button rendering: any administered organization is enough
            return self._is_any_organization_admin(user)
        return user.is_organization_admin_of(obj.organization)

    def has_view_permission(self, request: DjangoRequest, obj: OrganizationMembership | None = None) -> bool:
        # deliberately does NOT fall through to the Django model permission: membership rows are
        # visible to superusers and organization admins only, regardless of any `view`/`change`
        # grant a group may carry.
        return self._may_write(request, obj)

    def has_add_permission(self, request: DjangoRequest, obj: OrganizationMembership | None = None) -> bool:
        return self._may_write(request, obj)

    def has_change_permission(self, request: DjangoRequest, obj: OrganizationMembership | None = None) -> bool:
        return self._may_write(request, obj)

    def get_queryset(self, request: DjangoRequest):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # scoped to ADMINISTERED organizations, not merely joined ones -- this queryset also
        # backs get_object(), so it bounds which memberships the change view can load.
        return qs.filter(organization__in=request.user.admin_organizations)

    def formfield_for_foreignkey(self, db_field: Model, request: DjangoRequest, **kwargs):
        if db_field.name == "organization" and not request.user.is_superuser:
            administered = request.user.admin_organizations
            kwargs["queryset"] = administered
            if administered.count() == 1:
                # a single-organization admin does not choose. `disabled` makes Django ignore
                # whatever the POST carries for this field and clean to `initial` instead
                # (forms/fields.py Field.clean via BaseForm._clean_fields), so the value is
                # both pre-filled and unforgeable.
                kwargs["initial"] = administered.first().pk
                kwargs["disabled"] = True
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request: DjangoRequest, obj: OrganizationMembership, form: Form, change: bool) -> None:
        # Defense in depth: the scoped `organization` choices above are bypassable by a forged
        # POST whenever the field is a live selector, so re-check before writing.
        if not request.user.is_organization_admin_of(obj.organization):
            raise PermissionDenied(f"User({request.user}) is not an organization admin of {obj.organization}")
        super().save_model(request, obj, form, change)

    def get_user_github_login(self, obj: OrganizationMembership) -> str:
        return obj.user.github_login

    get_user_github_login.short_description = _("Github Login")


@admin.register(OrganizationInvite)
class OrganizationInviteAdmin(AllowIsStaffReadonlyMixin, UserCreatedBaseModelAdmin):
    """Invites are visible and writable only to superusers and organization admins, scoped to the organizations they administer.

    Both the listed rows (`get_queryset`) and the organization selector
    (`formfield_for_foreignkey`) are scoped to `KippoUser.admin_organizations`. Plain
    membership grants nothing here: an admin of one organization who merely belongs to a
    second must not read that second organization's invitee email addresses. See
    kiconiaworks/kippo#57.
    """

    list_display = ("organization", "email", "expiration_date", "is_complete", "expiration_date", "processed_datetime")
    ordering = ("organization", "email")
    search_fields = ["email"]

    @staticmethod
    def _is_any_organization_admin(user: KippoUser) -> bool:
        if not user.is_active or user.is_anonymous:
            return False
        return user.is_superuser or user.admin_organizations.exists()

    def _may_write(self, request: DjangoRequest, obj: OrganizationInvite | None = None) -> bool:
        user = request.user
        if not user.is_active or user.is_anonymous:
            return False
        if user.is_superuser:
            return True
        if obj is None:
            # changelist/add-button rendering: any administered organization is enough
            return self._is_any_organization_admin(user)
        return user.is_organization_admin_of(obj.organization)

    def has_add_permission(self, request: DjangoRequest, obj: OrganizationInvite | None = None) -> bool:
        return self._may_write(request, obj)

    def has_change_permission(self, request: DjangoRequest, obj: OrganizationInvite | None = None) -> bool:
        return self._may_write(request, obj)

    def has_delete_permission(self, request: DjangoRequest, obj: OrganizationInvite | None = None) -> bool:
        return self._may_write(request, obj)

    def has_view_permission(self, request: DjangoRequest, obj: OrganizationInvite | None = None) -> bool:
        # AllowIsStaffReadonlyMixin never defined has_view_permission, so without this an
        # organization admin would still need an explicit `accounts.view_organizationinvite`
        # grant to reach the changelist.
        if self._is_any_organization_admin(request.user):
            return True
        return super().has_view_permission(request, obj)

    def get_queryset(self, request: DjangoRequest):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # scoped to ADMINISTERED organizations, not merely joined ones -- this queryset also
        # backs get_object(), so it bounds which invites the change/delete views can load.
        return qs.filter(organization__in=request.user.admin_organizations)

    def formfield_for_foreignkey(self, db_field: Model, request: DjangoRequest, **kwargs):
        if db_field.name == "organization" and not request.user.is_superuser:
            kwargs["queryset"] = request.user.admin_organizations
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request: DjangoRequest, obj: OrganizationInvite, form: Form, change: bool) -> None:
        # Defense in depth: the scoped `organization` choices above are bypassable by a forged
        # POST, so re-check the target organization before writing.
        if not request.user.is_organization_admin_of(obj.organization):
            raise PermissionDenied(f"User({request.user}) is not an organization admin of {obj.organization}")
        super().save_model(request, obj, form, change)
        if not change:
            from django.conf import settings

            login_url = f"{settings.HOST_URL}{settings.URL_PREFIX}/admin/"
            self.message_user(request, f"Ask invited user to login using {obj.email} at: {login_url}", level=messages.INFO)


class KippoOrganizationAdminForm(forms.ModelForm):
    """Custom form for KippoOrganization that dynamically populates GitHub project template choices."""

    class Meta:
        model = KippoOrganization
        exclude = ()  # noqa: DJ006 (admin form inherits field config from ModelAdmin)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Use self.instance (set by ModelForm.__init__) instead of kwargs.get("instance")
        instance = self.instance
        choices = [("", _("--- No template (create blank project) ---"))]
        help_text = _("GitHub ProjectsV2 node ID to use as template when creating projects")

        # Get the current value from the instance
        current_value = ""
        if instance and instance.pk:
            current_value = instance.default_github_project_template or ""

        # Track project IDs that we've added to choices
        added_project_ids = {""}

        if instance and instance.pk and instance.github_organization_name:
            try:
                token = instance.githubaccesstoken.token
                projects = get_organization_projects_v2(instance.github_organization_name, token)
                logger.debug(f"Fetched {len(projects)} GitHub projects for {instance.github_organization_name}")
                for project in projects:
                    project_id = project["id"]
                    label = f"{project['title']} ({project_id})"
                    choices.append((project_id, label))
                    added_project_ids.add(project_id)
            except GithubAccessToken.DoesNotExist:
                logger.warning(f"No GitHub access token for organization: {instance.name}")
                help_text = _("No GitHub access token configured. Add a token to see available templates.")
            except GithubGraphQLError as e:
                error_str = str(e)
                if "INSUFFICIENT_SCOPES" in error_str or "read:project" in error_str:
                    logger.warning(f"GitHub token missing 'read:project' scope for organization: {instance.name}")
                    help_text = _(
                        "GitHub token missing 'read:project' scope. Update token at https://github.com/settings/tokens to see available templates."
                    )
                else:
                    logger.exception(f"GitHub GraphQL error for organization: {instance.name}")
                    help_text = _("Failed to fetch GitHub projects. Check logs for details.")
            except Exception:
                logger.exception(f"Failed to fetch GitHub projects for organization: {instance.name}")

        # If current value exists but wasn't in the fetched projects, add it to preserve the selection
        if current_value and current_value not in added_project_ids:
            choices.append((current_value, f"(Previously selected: {current_value})"))

        self.fields["default_github_project_template"] = forms.ChoiceField(
            choices=choices,
            required=False,
            initial=current_value,
            help_text=help_text,
        )

        # default_columnset: scope choices to this org's columnsets (+ shared/global org-null ones)
        if "default_columnset" in self.fields:
            if instance and instance.pk:
                self.fields["default_columnset"].queryset = ProjectColumnSet.objects.filter(Q(organization=instance) | Q(organization__isnull=True))
            else:
                self.fields["default_columnset"].queryset = ProjectColumnSet.objects.filter(organization__isnull=True)


@admin.register(KippoOrganization)
class KippoOrganizationAdmin(AllowIsStaffReadonlyMixin, OrganizationQuerysetModelAdminMixin, UserCreatedBaseModelAdmin):
    form = KippoOrganizationAdminForm
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

    def has_view_permission(self, request: DjangoRequest, obj: Model | None = None) -> bool:
        # Staff get read access (writes stay superuser-only via AllowIsStaffReadonlyMixin). Required for the
        # プロジェクトマネージャー autocomplete on the project admin: Django's autocomplete endpoint is served by
        # THIS admin and checks has_view_permission on it, so without this a non-superuser gets an empty
        # dropdown (403). Rows stay scoped to the user's own organizations via
        # OrganizationQuerysetModelAdminMixin.get_queryset -- staff never see other organizations' users.
        return self.check_perm(request.user)

    def get_search_results(self, request: DjangoRequest, queryset: QuerySet, search_term: str) -> tuple[QuerySet, bool]:
        # The org-scoped user autocomplete (プロジェクトマネージャー) pins its AJAX endpoint to one organization
        # via ?organization=<id> (OrganizationScopedAutocompleteSelect). Narrow the dropdown to that org's
        # members so it lists only candidates the project form will accept. get_queryset already restricts
        # non-superusers to their own orgs, so this can only narrow the visible set (no leak).
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        organization_id = request.GET.get(KIPPOUSER_AUTOCOMPLETE_ORGANIZATION_PARAM)
        if organization_id:
            queryset = queryset.filter(organizationmembership__organization_id=organization_id)
            may_have_duplicates = True
        return queryset, may_have_duplicates

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


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(AllowIsStaffReadonlyMixin, admin.ModelAdmin):
    list_display = (
        "get_created_by_display_name",
        "entry_datetime",
        "category",
    )

    def has_change_permission(self, request: DjangoRequest, obj: Model | None = None) -> bool:
        # only allow superuser to change attendance records
        return request.user.is_superuser

    @admin.display(description=_("ユーザー表示名"))
    def get_created_by_display_name(self, obj: AttendanceRecord | None = None) -> str:
        if obj and obj.created_by:
            return obj.created_by.display_name
        return "-"


# @admin.register(StartAttendanceRecord)
# class StartAttendanceRecordAdmin()


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


@admin.register(SlackCommand)
class SlackCommandAdmin(AllowIsSuperuserAdminMixin, admin.ModelAdmin):
    list_display = ("id", "organization", "user", "sub_command", "is_valid", "text", "created_datetime")


admin.site.unregister(UserSocialAuth)
admin.site.unregister(Nonce)
admin.site.unregister(Association)
admin.site.unregister(Group)
