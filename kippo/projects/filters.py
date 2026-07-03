from typing import TYPE_CHECKING

from django.contrib import admin
from django.db import models
from django.http import request as DjangoRequest  # noqa: N812
from django.utils.translation import gettext_lazy as _

from .models import DEFAULT_ACTIVE_PROJECT_PHASES, VALID_PROJECT_PHASES

if TYPE_CHECKING:
    from django.contrib.admin.views.main import ChangeList


class PhaseMultiSelectListFilter(admin.SimpleListFilter):
    """Multi-select フェーズ filter for the active-project changelist.

    Django's built-in field filter is single-select; this renders each phase as a toggle so several
    can be active at once. With no `phase` query param the two in-flight phases are pre-selected
    (DEFAULT_ACTIVE_PROJECT_PHASES); the 全て option clears to an explicit empty param so the defaults
    don't re-apply.
    """

    title = _("フェーズ")
    parameter_name = "phase"

    def lookups(self, request: DjangoRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        return list(VALID_PROJECT_PHASES)

    def selected_phases(self) -> list[str]:
        # value() is None only when the param is absent -> fall back to the defaults; an empty string
        # (user cleared the selection via 全て or by deselecting the last phase) means "no filter".
        value = self.value()
        if value is None:
            return list(DEFAULT_ACTIVE_PROJECT_PHASES)
        return [phase for phase in value.split(",") if phase]

    def queryset(self, request: DjangoRequest, queryset: models.QuerySet) -> models.QuerySet:
        selected = self.selected_phases()
        return queryset.filter(phase__in=selected) if selected else queryset

    def choices(self, changelist: "ChangeList"):
        selected = set(self.selected_phases())
        yield {
            "selected": not selected,
            "query_string": changelist.get_query_string({self.parameter_name: ""}),
            "display": _("全て"),
        }
        for lookup, title in self.lookup_choices:
            phase = str(lookup)
            toggled = selected ^ {phase}  # add if absent, remove if present
            yield {
                "selected": phase in selected,
                "query_string": changelist.get_query_string({self.parameter_name: ",".join(sorted(toggled))}),
                "display": title,
            }
