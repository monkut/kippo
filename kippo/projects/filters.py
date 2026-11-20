from commons.filters import MultiSelectListFilter
from django.contrib import admin
from django.db import models
from django.http import request as DjangoRequest  # noqa: N812
from django.utils.translation import gettext_lazy as _

from .definitions import NON_PROJECT_CATEGORY_VALUE
from .models import DEFAULT_ACTIVE_PROJECT_PHASES, VALID_PROJECT_PHASES, KippoProject, ProjectParticipant


class PhaseMultiSelectListFilter(MultiSelectListFilter):
    """Multi-select フェーズ filter, shared by the active-project (実行中) and sales (営業中) changelists.

    With no `phase` query param the in-flight pipeline is pre-selected
    (DEFAULT_ACTIVE_PROJECT_PHASES): the contracted block, 口頭受注 and the 提案 phases —
    a 完了 row on 実行中 is one that was never closed.

    The unselected remainder is KIT / 失注. On 営業中 that means KIT rows (which ARE in
    SalesKippoProjectManager's queryset) need an explicit toggle to show — so editing
    DEFAULT_ACTIVE_PROJECT_PHASES changes the default view of BOTH changelists.
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


PROJECT_ID_PARAM = "project__id__exact"


class ParticipantListFilter(admin.SimpleListFilter):
    """参加者 filter for the 月別割当 changelist (kippo#75), opened from a participant's N件 link.

    Choices are the participants of the project in ``?project__id__exact=`` (falling back to the selected
    participant's project). The filter's すべて option drops only ``participant``, so the project filter stays
    and every participant's rows for that project are listed. Hidden when no project is selected.
    """

    title = _("参加者")
    parameter_name = "participant"

    def __init__(self, request: DjangoRequest, params: dict, model: type[models.Model], model_admin: admin.ModelAdmin) -> None:
        project_id = request.GET.get(PROJECT_ID_PARAM)
        if not project_id and request.GET.get(self.parameter_name, "").isdigit():
            project_id = ProjectParticipant.objects.filter(pk=request.GET[self.parameter_name]).values_list("project_id", flat=True).first()
        self.project = KippoProject.objects.filter(pk=project_id).first() if project_id else None
        super().__init__(request, params, model, model_admin)
        if self.project is not None:
            self.title = _("参加者 (%(project)s)") % {"project": self.project.name}

    def lookups(self, request: DjangoRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        if self.project is None:
            return []
        participants = self.project.participants.select_related("user").order_by("user__username")
        return [(str(p.pk), p.user.display_name.strip() or p.user.username) for p in participants]

    def queryset(self, request: DjangoRequest, queryset: models.QuerySet) -> models.QuerySet:
        return queryset.filter(participant_id=self.value()) if self.value() else queryset
