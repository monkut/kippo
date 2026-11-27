"""Employee survey result admins."""

import urllib.parse

from commons.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from commons.widgets import MonthYearWidget
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.core.exceptions import ValidationError
from django.db import models
from django.forms import Form
from django.http import (
    HttpRequest,
    HttpResponseRedirect,
    request as DjangoRequest,  # noqa: N812
)
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..functions import (
    generate_kippoprojectusermonthlystatisfaction_csv,
    generate_kippoprojectuserstatisfactionresult_csv,
)
from ..models import (
    KippoProjectUserMonthlyStatisfactionResult,
    KippoProjectUserStatisfactionResult,
)
from .constants import SURVEY_SCOPE_MONTHLY, SURVEY_SCOPE_RETROSPECTIVE
from .widgets import SurveyScopedProjectAutocompleteSelect, survey_project_display_label, survey_project_queryset


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
    # プロジェクト is selected via a searchable autocomplete (searches KippoProject.name through
    # KippoProjectAdmin.search_fields) instead of a long unsearchable <select>. The dropdown is pinned to
    # this survey's scope so it offers exactly what get_form validates against.
    autocomplete_fields = ("project",)
    SURVEY_SCOPE = SURVEY_SCOPE_RETROSPECTIVE

    def formfield_for_foreignkey(self, db_field: models.Field, request: DjangoRequest, **kwargs):
        if db_field.name == "project" and "project" in self.get_autocomplete_fields(request):
            kwargs["widget"] = SurveyScopedProjectAutocompleteSelect(
                db_field, self.admin_site, using=kwargs.get("using"), survey_scope=self.SURVEY_SCOPE
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_dbfield(self, db_field: models.Field, request: DjangoRequest, **kwargs):
        # The survey form only *selects* a プロジェクト -- drop the ＋ / ✎ (add / change) links the admin puts
        # beside a related select, so it is not a side door into project maintenance. Wrapping happens in
        # formfield_for_dbfield (after formfield_for_foreignkey), so the flags have to be cleared here.
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "project" and isinstance(getattr(formfield, "widget", None), RelatedFieldWidgetWrapper):
            formfield.widget.can_add_related = False
            formfield.widget.can_change_related = False
        return formfield

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

        if "project" in form.base_fields:
            open_projects = survey_project_queryset(request.user, self.SURVEY_SCOPE)
            form.base_fields["project"].initial = open_projects.first()
            form.base_fields["project"].queryset = open_projects
            form.base_fields["project"].label_from_instance = survey_project_display_label
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
    # Searchable プロジェクト select, pinned to this survey's scope (非案件 rows) — see the retrospective admin.
    autocomplete_fields = ("project",)
    SURVEY_SCOPE = SURVEY_SCOPE_MONTHLY

    def formfield_for_foreignkey(self, db_field: models.Field, request: DjangoRequest, **kwargs):
        if db_field.name == "project" and "project" in self.get_autocomplete_fields(request):
            kwargs["widget"] = SurveyScopedProjectAutocompleteSelect(
                db_field, self.admin_site, using=kwargs.get("using"), survey_scope=self.SURVEY_SCOPE
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

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

        if "project" in form.base_fields:
            open_projects = survey_project_queryset(request.user, self.SURVEY_SCOPE)
            form.base_fields["project"].initial = open_projects.first()
            form.base_fields["project"].queryset = open_projects
            form.base_fields["project"].label_from_instance = survey_project_display_label
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
