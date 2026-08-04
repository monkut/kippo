import json
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.contrib.admin.views.autocomplete import AutocompleteJsonView
from django.db.models import Model, QuerySet
from django.forms import BaseFormSet, Form, widgets
from django.http import (
    HttpResponse,
    request as DjangoRequest,  # noqa: N812
)

from commons.functions import ui_url

if TYPE_CHECKING:
    from accounts.models import KippoUser


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

    def check_perm(self, user_obj: "KippoUser"):
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
    def check_perm(self, user_obj: "KippoUser"):
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
    def check_perm(self, user_obj: "KippoUser"):
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


class KippoAutocompleteJsonView(AutocompleteJsonView):
    """Autocomplete results labelled by the ModelAdmin rather than always by ``str(obj)``.

    A ModelAdmin opts in by defining ``autocomplete_result_label(obj)``. Needed where a form labels the
    select with something other than __str__ (KippoProjectBaseAdmin: project.name, vs. the model's
    "KippoProject(顧客名 名前)") -- without it the option text would change the moment a row is picked
    from the dropdown.
    """

    def serialize_result(self, obj: Model, to_field_name: str) -> dict:
        result = super().serialize_result(obj, to_field_name)
        get_label = getattr(self.model_admin, "autocomplete_result_label", None)
        if get_label:
            result["text"] = get_label(obj)
        return result


class KippoAdminSite(admin.AdminSite):
    # update displayed header/title
    site_header = settings.SITE_HEADER
    site_title = settings.SITE_TITLE
    site_url = ui_url("weekly-effort")

    # apps pinned to the top of the admin index, in this order; remaining apps keep Django's default order
    APP_PRIORITY = ("customers", "projects")

    def autocomplete_view(self, request: DjangoRequest) -> HttpResponse:
        # site-wide endpoint (there is no per-ModelAdmin autocomplete view) -- swapped for the subclass
        # that honours each admin's autocomplete_result_label.
        return KippoAutocompleteJsonView.as_view(admin_site=self)(request)

    def _app_sort_key(self, app: dict) -> int:
        app_label = app["app_label"]
        if app_label in self.APP_PRIORITY:
            return self.APP_PRIORITY.index(app_label)
        return len(self.APP_PRIORITY)

    def get_app_list(self, request: DjangoRequest, app_label: str | None = None) -> list:
        # stable sort keeps Django's default order for apps not in APP_PRIORITY
        return sorted(super().get_app_list(request, app_label), key=self._app_sort_key)


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
