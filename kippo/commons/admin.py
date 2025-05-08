import json

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.contrib.auth.models import AbstractUser
from django.db.models import Model, QuerySet
from django.forms import BaseFormSet, Form, widgets
from django.http import request as DjangoRequest  # noqa: N812


class KippoAdminConfig(AdminConfig):
    default_site = "commons.admin.KippoAdminSite"


class UserCreatedBaseModelAdmin(admin.ModelAdmin):
    def save_model(self, request: DjangoRequest, obj: Model, form: Form, change: bool):
        if getattr(obj, "pk", None) is None:
            obj.created_by = request.user
            obj.updated_by = request.user
        else:
            obj.updated_by = request.user
        obj.save()

    def save_formset(self, request: DjangoRequest, form: Form, formset: BaseFormSet, change: bool):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if instance.id is None:
                instance.created_by = request.user  # only update created_by once!

            instance.updated_by = request.user
            instance.save()
        formset.save_m2m()


class AllowIsStaffAdminMixin:
    """NOTE: Must be placed BEFORE admin.ModelAdmin"""

    def check_perm(self, user_obj: AbstractUser):
        if not user_obj.is_active or user_obj.is_anonymous:
            return False
        return user_obj.is_superuser or user_obj.is_staff

    def has_view_permission(self, request: DjangoRequest, obj: Model | None = None):
        return self.check_perm(request.user)

    def has_add_permission(self, request: DjangoRequest, obj: Model | None = None):  # inline has_add_permission passes object
        return self.check_perm(request.user)

    def has_change_permission(self, request: DjangoRequest, obj: Model | None = None):
        return self.check_perm(request.user)

    def has_delete_permission(self, request: DjangoRequest, obj: Model | None = None):
        return self.check_perm(request.user)

    def has_module_permission(self, request: DjangoRequest):
        return self.check_perm(request.user)


class AllowIsStaffReadonlyMixin:
    def check_perm(self, user_obj: AbstractUser):
        if not user_obj.is_active or user_obj.is_anonymous:
            return False
        return user_obj.is_superuser or user_obj.is_staff

    def has_add_permission(self, request: DjangoRequest, obj: Model | None = None):  # inline has_add_permission passes object
        if not request.user.is_active or request.user.is_anonymous:
            return False
        return request.user.is_superuser

    def has_change_permission(self, request: DjangoRequest, obj: Model | None = None):
        if not request.user.is_active or request.user.is_anonymous:
            return False
        return request.user.is_superuser

    def has_delete_permission(self, request: DjangoRequest, obj: Model | None = None):
        if not request.user.is_active or request.user.is_anonymous:
            return False
        return request.user.is_superuser

    def has_module_permission(self, request: DjangoRequest, obj: Model | None = None):
        return self.check_perm(request.user)


class AllowIsSuperuserAdminMixin:
    def check_perm(self, user_obj: AbstractUser):
        if not user_obj.is_active or user_obj.is_anonymous:
            return False
        return user_obj.is_superuser

    def has_add_permission(self, request: DjangoRequest, obj: Model | None = None):  # inline has_add_permission passes object
        return self.check_perm(request.user)

    def has_change_permission(self, request: DjangoRequest, obj: Model | None = None):
        return self.check_perm(request.user)

    def has_delete_permission(self, request: DjangoRequest, obj: Model | None = None):
        return self.check_perm(request.user)

    def has_module_permission(self, request: DjangoRequest):
        return self.check_perm(request.user)


class OrganizationTaskQuerysetModelAdminMixin:
    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(project__organization__in=request.user.organizations).order_by("project__organization").distinct()


class OrganizationQuerysetModelAdminMixin:
    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # get user organizations
        return (
            qs.filter(organizationmembership__organization__in=request.user.organizations).order_by("organizationmembership__organization").distinct()
        )


class KippoAdminSite(admin.AdminSite):
    # update displayed header/title
    site_header = settings.SITE_HEADER
    site_title = settings.SITE_TITLE
    site_url = f"{settings.URL_PREFIX}/projects/"


admin_site = KippoAdminSite(name="kippoadmin")


class PrettyJSONWidget(widgets.Textarea):
    def format_value(self, value: str) -> str:
        try:
            value = json.dumps(json.loads(value), indent=4, ensure_ascii=False, sort_keys=True)
        except json.JSONDecodeError:
            return super().format_value(value)
        # these lines will try to adjust size of TextArea to fit to content
        row_lengths = [len(r) for r in value.split("\n")]
        self.attrs["rows"] = min(max(len(row_lengths) + 2, 10), 30)
        self.attrs["cols"] = min(max(max(row_lengths) + 2, 50), 120)
        return value
