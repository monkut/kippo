from django.contrib import admin
from django.utils import timezone
from django.contrib import messages
from django.utils.translation import ugettext_lazy as _
from django.contrib.auth.models import Group
from social_django.models import Association, Nonce, UserSocialAuth

from common.admin import UserCreatedBaseModelAdmin, AllowIsStaffAdminMixin, AllowIsStaffReadonlyMixin, AllowIsSuperuserAdminMixin, OrganizationQuerysetModelAdminMixin
from octocat.models import GithubAccessToken
from projects.models import CollectIssuesAction
from projects.functions import collect_existing_github_projects
from tasks.periodic.tasks import collect_github_project_issues

from .models import (
    EmailDomain,
    KippoOrganization,
    KippoUser,
    OrganizationMembership,
    PersonalHoliday,
    Country,
    PublicHoliday
)


class EmailDomainAdminReadOnlyInline(admin.TabularInline):
    model = EmailDomain
    extra = 0
    fields = (
        'domain',
        'is_staff_domain',
        'updated_by',
        'updated_datetime',
        'created_by',
        'created_datetime',
    )
    readonly_fields = (
        'domain',
        'is_staff_domain',
        'updated_by',
        'updated_datetime',
        'created_by',
        'created_datetime',
    )

    def has_add_permission(self, request, obj):  # so that 'add button' is not available in admin
        return False

    def get_queryset(self, request):
        # update so that Milestones are displayed in expected delivery order
        qs = super().get_queryset(request).order_by('created_datetime')
        return qs


class EmailDomainAdminInline(admin.TabularInline):
    model = EmailDomain
    extra = 0
    fields = (
        'domain',
        'is_staff_domain',
    )

    def get_queryset(self, request):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


class GithubAccessTokenAdminReadOnlyInline(admin.StackedInline):
    model = GithubAccessToken
    exclude = ('token', )
    fields = (
        'created_by',
        'created_datetime',
    )
    readonly_fields = (
        'created_by',
        'created_datetime',
    )

    def has_add_permission(self, request, obj):
        return False


class GithubAccessTokenAdminInline(admin.StackedInline):
    model = GithubAccessToken
    extra = 0

    def get_queryset(self, request):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs

    def has_add_permission(self, request, obj):
        return True


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        'organization',
        'user',
        'committed_days',
        'is_project_manager',
        'is_developer',
    )
    ordering = (
        'organization',
        'user',
    )


@admin.register(KippoOrganization)
class KippoOrganizationAdmin(AllowIsStaffReadonlyMixin, OrganizationQuerysetModelAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        'name',
        'id',
        'github_organization_name',
        'default_task_category',
        'google_forms_project_survey_url',
        'webhook_secret',
        'webhook_url',
        'updated_by',
        'updated_datetime',
        'created_by',
        'created_datetime',
    )
    search_fields = (
        'name',
    )
    inlines = (
        GithubAccessTokenAdminReadOnlyInline,
        GithubAccessTokenAdminInline,
        EmailDomainAdminReadOnlyInline,
        EmailDomainAdminInline,
    )
    actions = [
        'collect_organization_projects_action',
        'collect_github_project_issues_action'
    ]

    def collect_organization_projects_action(self, request, queryset):
        for organization in queryset:
            added_projects = collect_existing_github_projects(
                organization=organization,
                as_user=request.user
            )

            projects_string = ', '.join(p.name for p in added_projects)
            msg = f'Added [{organization.name}] ({len(added_projects)}) {projects_string}'
            self.message_user(
                request,
                msg,
                level=messages.INFO
            )
    collect_organization_projects_action.short_description = _('Collect Organization Project(s)')

    def collect_github_project_issues_action(self, request, queryset):
        status_effort_date = timezone.now().isoformat()
        for organization in queryset:
            action_tracker = CollectIssuesAction(
                organization=organization,
                created_by=request.user,
                updated_by=request.user,
            )
            action_tracker.save()
            collect_github_project_issues(
                action_tracker_id=action_tracker.id,
                kippo_organization_id=str(organization.id),
                status_effort_date_iso8601=status_effort_date
            )
            self.message_user(
                request,
                f'Processing Request: CollectIssuesAction(id={action_tracker.id})',
                level=messages.INFO
            )
    collect_github_project_issues_action.short_description = _('Collect Organization Project Issues')


@admin.register(KippoUser)
class KippoUserAdmin(AllowIsStaffReadonlyMixin, OrganizationQuerysetModelAdminMixin, admin.ModelAdmin):
    list_display = (
        'username',
        'id',
        'github_login',
        'get_github_organizations',
        'last_name',
        'first_name',
        'holiday_country',
        'date_joined',
        'last_login',
        'is_github_outside_collaborator',
        'is_staff',
        'is_superuser',
    )
    exclude = ('user_permissions', 'groups', 'last_login', )

    def get_is_collaborator(self, obj):
        return obj.is_github_outside_collaborator
    get_is_collaborator.short_description = _('Is Collaborator')

    def get_github_organizations(self, obj):
        membership_organizations = []
        for organization in obj.memberships.all():
            name = organization.github_organization_name
            membership_organizations.append(name)
        return ', '.join(membership_organizations)
    get_github_organizations.short_description = _('Github Organizations')


@admin.register(PersonalHoliday)
class PersonalHolidayAdmin(AllowIsStaffAdminMixin, admin.ModelAdmin):
    list_display = (
        'user',
        'is_half',
        'day',
        'duration',
    )

    def save_model(self, request, obj, form, change):
        if getattr(obj, 'pk', None) is None:
            obj.user = request.user
        obj.save()


@admin.register(Country)
class CountryAdmin(AllowIsStaffReadonlyMixin, admin.ModelAdmin):
    list_display = (
        'name',
        'alpha_2',
        'alpha_3',
        'country_code',
        'region'
    )


@admin.register(PublicHoliday)
class PublicHolidayAdmin(AllowIsStaffReadonlyMixin, admin.ModelAdmin):
    list_display = (
        'name',
        'country',
        'day',
    )


#@admin.site.unregister(UserSocialAuth)
admin.site.unregister(Nonce)
admin.site.unregister(Association)
admin.site.unregister(Group)
