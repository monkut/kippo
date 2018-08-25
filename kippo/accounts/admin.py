from django.contrib import admin
from django.urls import resolve
from social_django.models import Association, Nonce, UserSocialAuth
from django.contrib.auth.models import Permission, Group
from common.admin import UserCreatedBaseModelAdmin
from octocat.models import GithubAccessToken
from .models import EmailDomain, KippoOrganization, KippoUser, PersonalHoliday


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

    # def get_max_num(self, request, obj=None, **kwargs):
    #     # seems to work sporatically...
    #     max_num = 1
    #     resolved_path = resolve(request.path)
    #     object_id = resolved_path.kwargs.get('object_id', None)
    #     if object_id and GithubAccessToken.objects.filter(pk=int(object_id)).exists():
    #         max_num = 0
    #     return max_num


class KippoOrganizationAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        'name',
        'github_organization_name',
        'default_task_category',
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


class KippoUserAdmin(admin.ModelAdmin):
    list_display = (
        'username',
        'github_login',
        'last_name',
        'first_name',
        'is_project_manager',
        'is_developer',
        'date_joined',
        'last_login',
        'is_staff',
        'is_superuser',
    )


class PersonalHolidayAdmin(admin.ModelAdmin):
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


admin.site.register(KippoOrganization, KippoOrganizationAdmin)
admin.site.register(KippoUser, KippoUserAdmin)
admin.site.register(PersonalHoliday, PersonalHolidayAdmin)


admin.site.unregister(UserSocialAuth)
admin.site.unregister(Nonce)
admin.site.unregister(Association)

#admin.site.register(Permission)
#admin.site.unregister(Group)
