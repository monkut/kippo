from collections.abc import Iterator
from typing import TYPE_CHECKING

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from django.contrib.admin.views.main import ChangeList


class MultiSelectListFilter(admin.SimpleListFilter):
    """Reusable base for multi-select toggle changelist filters.

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
