from collections.abc import Iterator
from typing import TYPE_CHECKING

from django.contrib import admin
from django.db import models
from django.http import request as DjangoRequest  # noqa: N812
from django.utils.translation import gettext_lazy as _

from .definitions import NON_PROJECT_CATEGORY_VALUE
from .models import DEFAULT_ACTIVE_PROJECT_PHASES, VALID_PROJECT_PHASES

if TYPE_CHECKING:
    from django.contrib.admin.views.main import ChangeList


class _MultiSelectListFilter(admin.SimpleListFilter):
    """Base for the multi-select toggle filters on the project changelists.

    Django's built-in field filter is single-select; this renders each lookup as a toggle so several
    can be active at once, plus a 全て option that clears to an explicit empty param. With no query
    param `default_values()` is pre-selected; the explicit empty param (全て, or deselecting the last
    toggle) overrides that default so it does not re-apply. Subclasses supply `lookups()`, `queryset()`
    (include vs exclude semantics differ), and optionally `default_values()`.
    """

    def default_values(self) -> list[str]:
        # Applied when the query param is absent. Empty means "no default selection".
        return []

    def selected_values(self) -> list[str]:
        # value() is None only when the param is absent -> fall back to the defaults; an explicit empty
        # string (全て or deselecting the last toggle) means "no selection".
        value = self.value()
        if value is None:
            return self.default_values()
        return [item for item in value.split(",") if item]

    def choices(self, changelist: "ChangeList") -> Iterator[dict]:
        selected = set(self.selected_values())
        yield {
            "selected": not selected,
            "query_string": changelist.get_query_string({self.parameter_name: ""}),
            "display": _("全て"),
        }
        for lookup, title in self.lookup_choices:
            item = str(lookup)
            toggled = selected ^ {item}  # add if absent, remove if present
            yield {
                "selected": item in selected,
                "query_string": changelist.get_query_string({self.parameter_name: ",".join(sorted(toggled))}),
                "display": title,
            }


class PhaseMultiSelectListFilter(_MultiSelectListFilter):
    """Multi-select フェーズ filter for the active-project changelist.

    With no `phase` query param the two in-flight phases are pre-selected (DEFAULT_ACTIVE_PROJECT_PHASES).
    """

    title = _("フェーズ")
    parameter_name = "phase"

    def default_values(self) -> list[str]:
        return list(DEFAULT_ACTIVE_PROJECT_PHASES)

    def lookups(self, request: DjangoRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        return list(VALID_PROJECT_PHASES)

    def queryset(self, request: DjangoRequest, queryset: models.QuerySet) -> models.QuerySet:
        selected = self.selected_values()
        return queryset.filter(phase__in=selected) if selected else queryset


class CategoryExcludeListFilter(_MultiSelectListFilter):
    """Multi-select カテゴリ *exclude* filter for the active-project changelist.

    The inverse of the include filters above: a selected (highlighted) toggle drops that category's
    rows from the changelist. With no query param the 非案件 (non-project) category is excluded by
    default; the 全て option clears to an explicit empty param so every category shows.
    """

    title = _("カテゴリ (除外)")
    parameter_name = "exclude_category"

    def default_values(self) -> list[str]:
        return [NON_PROJECT_CATEGORY_VALUE]

    def lookups(self, request: DjangoRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        # Offer only the categories present on the rows the admin queryset already scopes to the user;
        # keyed by category__key (labels are the org's copy of the shared label). Ordering by (key, label)
        # makes the deduped label deterministic when one key carries divergent labels across orgs.
        pairs = (
            model_admin.get_queryset(request)
            .exclude(category__isnull=True)
            .values_list("category__key", "category__label")
            .order_by("category__key", "category__label")
            .distinct()
        )
        deduped: dict[str, str] = {}
        for key, label in pairs:
            deduped.setdefault(key, label)
        return list(deduped.items())

    def queryset(self, request: DjangoRequest, queryset: models.QuerySet) -> models.QuerySet:
        excluded = self.selected_values()
        return queryset.exclude(category__key__in=excluded) if excluded else queryset
