"""Organization- and survey-scoped autocomplete widgets."""

import urllib.parse
import uuid

from accounts.definitions import KIPPOUSER_AUTOCOMPLETE_ORGANIZATION_PARAM
from accounts.models import KippoUser
from django.contrib import admin
from django.contrib.admin.widgets import AutocompleteSelect
from django.db import models
from django.db.models import QuerySet

from ..definitions import (
    NON_PROJECT_CATEGORY_VALUE,
)
from ..models import (
    KippoProject,
)
from .definitions import CUSTOMER_AUTOCOMPLETE_ORGANIZATION_PARAM, SURVEY_PROJECT_AUTOCOMPLETE_SCOPE_PARAM, SURVEY_SCOPE_MONTHLY


class OrganizationScopedAutocompleteSelect(AutocompleteSelect):
    """AutocompleteSelect that pins its autocomplete AJAX request to one organization.

    Django's autocomplete dropdown is served by the RELATED model's admin (KippoCustomerAdmin for 顧客 /
    請求先, KippoUserAdmin for プロジェクトマネージャー), which does not know which project is being edited,
    so it otherwise lists every row the editor may see (all their orgs; all orgs for a superuser).
    Appending ``?<param_name>=<id>`` to the endpoint URL lets that admin's get_search_results narrow the
    dropdown to the project's organization. Select2 appends its own term/page params with ``&``,
    preserving this.
    """

    def __init__(
        self,
        *args,
        organization_id: uuid.UUID | None = None,
        param_name: str = CUSTOMER_AUTOCOMPLETE_ORGANIZATION_PARAM,
        **kwargs,
    ) -> None:
        self.organization_id = organization_id
        self.param_name = param_name
        super().__init__(*args, **kwargs)

    def get_url(self) -> str:
        url = super().get_url()
        if self.organization_id:
            url = f"{url}?{urllib.parse.urlencode({self.param_name: self.organization_id})}"
        return url


class SurveyScopedProjectAutocompleteSelect(AutocompleteSelect):
    """AutocompleteSelect that pins the employee-survey プロジェクト autocomplete to one survey scope.

    The dropdown is served by KippoProjectAdmin, which would otherwise list every project the editor may
    see — including closed ones and the wrong category, which the survey form then rejects on save.
    Appending ``?survey_scope=<scope>`` lets KippoProjectBaseAdmin.get_search_results apply the same
    filter the form field validates with.
    """

    def __init__(self, *args, survey_scope: str, **kwargs) -> None:
        self.survey_scope = survey_scope
        super().__init__(*args, **kwargs)

    def get_url(self) -> str:
        url = super().get_url()
        return f"{url}?{urllib.parse.urlencode({SURVEY_PROJECT_AUTOCOMPLETE_SCOPE_PARAM: self.survey_scope})}"


def survey_project_queryset(user: KippoUser, survey_scope: str) -> QuerySet:
    """Projects selectable on an employee-survey form, for ``survey_scope``.

    Single source of truth for both the form field queryset (which validates the submitted value) and the
    autocomplete dropdown (KippoProjectBaseAdmin.get_search_results), so the two cannot drift — a project
    offered in the dropdown is always one the form accepts.
    """
    queryset = KippoProject.objects.filter(is_closed=False, organization__in=user.organizations)
    if survey_scope == SURVEY_SCOPE_MONTHLY:
        queryset = queryset.filter(category__key=NON_PROJECT_CATEGORY_VALUE)
    else:
        queryset = queryset.exclude(category__key=NON_PROJECT_CATEGORY_VALUE)
    # customer joined in: it is part of the option label (survey_project_display_label).
    return queryset.select_related("customer").order_by("name")


def survey_project_display_label(project: KippoProject) -> str:
    """プロジェクト option text on the employee-survey selects: "{プロジェクト名} {顧客名}".

    Single source of truth for the dropdown results (KippoProjectBaseAdmin.autocomplete_result_label) and
    the selected option (label_from_instance on the survey forms), so the text cannot change the moment a
    row is picked. Projects without a 顧客 show the name alone.
    """
    return f"{project.name} {project.customer.name}" if project.customer else project.name


def _customer_autocomplete_widget(
    model_admin: admin.options.BaseModelAdmin, db_field: models.ForeignKey, organization_id: uuid.UUID | None, using: str | None
) -> OrganizationScopedAutocompleteSelect:
    """A KippoCustomer autocomplete widget whose dropdown is pinned to ``organization_id`` (or unscoped
    when it is None, e.g. on /add/ before a project organization exists). Shared by the project's 顧客
    field and the contract inline's 請求先 field.
    """
    return OrganizationScopedAutocompleteSelect(db_field, model_admin.admin_site, using=using, organization_id=organization_id)


def _project_manager_autocomplete_widget(
    model_admin: admin.options.BaseModelAdmin, db_field: models.ForeignKey, organization_id: uuid.UUID | None, using: str | None
) -> OrganizationScopedAutocompleteSelect:
    """A KippoUser autocomplete widget pinned to ``organization_id``: プロジェクトマネージャー and the
    プロジェクト参加者 inline's user.

    None (on /add/, before a project organization exists) leaves the dropdown scoped to whatever
    KippoUserAdmin.get_queryset allows — the editor's own organizations.
    """
    return OrganizationScopedAutocompleteSelect(
        db_field,
        model_admin.admin_site,
        using=using,
        organization_id=organization_id,
        param_name=KIPPOUSER_AUTOCOMPLETE_ORGANIZATION_PARAM,
    )
