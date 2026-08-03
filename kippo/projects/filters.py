from commons.filters import MultiSelectListFilter
from django.contrib import admin
from django.db import models
from django.http import request as DjangoRequest  # noqa: N812
from django.utils.translation import gettext_lazy as _

from .definitions import NON_PROJECT_CATEGORY_VALUE
from .models import DEFAULT_ACTIVE_PROJECT_PHASES, VALID_PROJECT_PHASES


class PhaseMultiSelectListFilter(MultiSelectListFilter):
    """Multi-select フェーズ filter for the active-project changelist.

    With no `phase` query param the in-flight phases plus 完了 are pre-selected
    (DEFAULT_ACTIVE_PROJECT_PHASES) — a 完了 row here is one that was never closed.
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


class CategoryExcludeListFilter(MultiSelectListFilter):
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
