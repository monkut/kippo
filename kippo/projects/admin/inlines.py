"""Inlines shown on the project and contract admin pages."""

import urllib.parse

import nested_admin
from accounts.models import KippoUser, OrganizationMembership
from commons.admin import AllowIsStaffAdminMixin
from customers.models import KippoCustomer
from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Prefetch, QuerySet
from django.forms import BaseFormSet, Form
from django.forms.models import BaseInlineFormSet
from django.http import (
    HttpRequest,
    request as DjangoRequest,  # noqa: N812
)
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from octocat.models import GithubRepository

from ..definitions import (
    BILLING_TYPE_MONTHLY,
    PRICING_BASIS_EFFORT,
    WEEKLY_EFFORT_CLOSED_MESSAGE,
    ProjectRoles,
)
from ..filters import PROJECT_ID_PARAM, ParticipantListFilter
from ..models import (
    KippoMilestone,
    KippoProject,
    KippoProjectBillingEntry,
    KippoProjectContract,
    KippoProjectPhaseConfidenceChange,
    KippoProjectStatus,
    ProjectAssignmentRate,
    ProjectMonthlyAssignment,
    ProjectParticipant,
    ProjectWeeklyEffort,
)
from .definitions import CONTRACT_REQUIRED_FOR_UNDER_CONTRACT_MSG
from .widgets import _customer_autocomplete_widget, _project_manager_autocomplete_widget


class LockWhenProjectClosedInlineMixin:
    """Inline mixin that disables add/change/delete when the parent KippoProject is closed."""

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None):
        if getattr(obj, "is_closed", False):
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request: DjangoRequest, obj: models.Model | None = None):
        if getattr(obj, "is_closed", False):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request: DjangoRequest, obj: models.Model | None = None):
        if getattr(obj, "is_closed", False):
            return False
        return super().has_delete_permission(request, obj)


class ProjectAssignmentRateInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, nested_admin.NestedTabularInline):
    model = ProjectAssignmentRate
    extra = 0
    # One rate per role (unique_together project+role) — cap rows at the number of defined roles so
    # no extra entries can be added beyond them. Stays in sync if ProjectRoles changes.
    max_num = len(ProjectRoles.choices())
    fields = ("role", "rate_per_day")

    # Hidden on /add/ (see KippoProjectBaseAdmin.HIDDEN_ON_ADD_INLINES) — the fixture defaults are
    # seeded in save_model on create. Shown on /change/ for editing the per-role rates.


class ProjectParticipantInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, nested_admin.NestedTabularInline):
    """プロジェクト参加者 (kippo#75). KippoProjectBaseAdmin.save_formset runs the participant services:
    add → rows for the remaining months (R1); percentage/role edit → remaining months updated (E2);
    delete → ProjectParticipant.delete() removes the remaining months (X1). Monthly rows are not shown here;
    each participant links to the ProjectMonthlyAssignment admin filtered to it.
    """

    model = ProjectParticipant
    extra = 0
    fields = ("user", "percentage", "role", "monthly_assignments_link")
    readonly_fields = ("monthly_assignments_link",)
    ordering = ("user__username",)
    # Searchable user select (KippoUserAdmin.search_fields), pinned to the project's organization members.
    autocomplete_fields = ("user",)

    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        # monthly_assignments_link renders every participant's months — fetch them in one query.
        months = ProjectMonthlyAssignment.objects.filter(month__isnull=False).order_by("month")
        return super().get_queryset(request).prefetch_related(Prefetch("monthly_assignments", queryset=months))

    def get_formset(self, request: DjangoRequest, obj: KippoProject | None = None, **kwargs):
        # Stash the project's org BEFORE super() builds the form so formfield_for_foreignkey can pin the
        # user autocomplete dropdown to it. The inline is only rendered on the change form, so obj is set.
        request._participant_user_autocomplete_organization_id = obj.organization_id if obj is not None else None
        formset = super().get_formset(request, obj, **kwargs)
        # closed projects render every field readonly → no "user" form field to scope.
        # Validation stays scoped to the org's members, so a tampered cross-org submission is rejected.
        if obj is not None and "user" in formset.form.base_fields:
            member_ids = OrganizationMembership.objects.filter(organization=obj.organization).values_list("user_id", flat=True)
            formset.form.base_fields["user"].queryset = KippoUser.objects.filter(id__in=member_ids).order_by("last_name", "username")
        return formset

    def formfield_for_foreignkey(self, db_field: models.ForeignKey, request: DjangoRequest, **kwargs):
        if db_field.name == "user" and "user" in self.get_autocomplete_fields(request):
            kwargs["widget"] = _project_manager_autocomplete_widget(
                self, db_field, getattr(request, "_participant_user_autocomplete_organization_id", None), kwargs.get("using")
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_dbfield(self, db_field: models.Field, request: DjangoRequest, **kwargs):
        # Participants are chosen from existing organization members only — drop the ＋ / ✎ / × / 👁 links the
        # admin puts beside a related select, so this is not a side door into user maintenance. Wrapping happens
        # in formfield_for_dbfield (after formfield_for_foreignkey), so the flags have to be cleared here.
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "user" and isinstance(getattr(formfield, "widget", None), RelatedFieldWidgetWrapper):
            formfield.widget.can_add_related = False
            formfield.widget.can_change_related = False
            formfield.widget.can_delete_related = False
            formfield.widget.can_view_related = False
        return formfield

    @admin.display(description=_("月別割当"))
    def monthly_assignments_link(self, obj: ProjectParticipant) -> str:
        if not obj.pk:
            return "-"
        # project + participant: the changelist's 参加者 filter can then be cleared to see the whole project.
        query = urllib.parse.urlencode({PROJECT_ID_PARAM: obj.project_id, ParticipantListFilter.parameter_name: obj.pk})
        url = f"{reverse('admin:projects_projectmonthlyassignment_changelist')}?{query}"
        # Prefetched month-ordered (get_queryset); the filter/sort keeps a non-prefetched obj correct too.
        assignments = sorted((a for a in obj.monthly_assignments.all() if a.month), key=lambda a: a.month)
        # "11月、12月" linked to the participant's changelist; each month's tooltip carries its year and
        # percentage (e.g. "2026年11月: 40%").
        months = format_html_join(
            "、",
            '<span title="{}年{}月: {}%">{}月</span>',
            ((a.month.year, a.month.month, a.percentage, a.month.month) for a in assignments),
        )
        return format_html('<a href="{}">{}</a>', url, months or "-")


class KippoProjectBillingEntryInline(AllowIsStaffAdminMixin, nested_admin.NestedTabularInline):
    # Billing entries belong to the contract (kippo#31). Rendered both directly on
    # KippoProjectContractAdmin and nested under KippoProjectContractInline on the project page.
    model = KippoProjectBillingEntry
    extra = 0
    fields = ("billing_date", "amount", "effort_actual", "is_manual", "is_received", "received_datetime", "received_by", "note")
    # received_datetime / received_by are auto-managed (stamped when is_received is ticked, cleared
    # when unticked) — shown read-only so typed values can't be silently discarded.
    readonly_fields = ("effort_actual", "received_datetime", "received_by")

    @admin.display(description=_("実績額"))
    def effort_actual(self, obj: KippoProjectBillingEntry) -> str:
        """Computed actual for the entry's billing month, shown beside the (possibly provisional 仮)
        amount so the operator can compare and correct before receiving (kippo#46).
        """
        if not obj.pk:
            return ""
        contract = obj.contract
        if not (contract.pricing_basis == PRICING_BASIS_EFFORT and contract.billing_type == BILLING_TYPE_MONTHLY):
            return "-"
        return f"¥{contract.effort_actual_amount(obj.billing_date):,}"


def _validate_under_contract_gate(formset: BaseFormSet) -> None:
    """Raise on the contract inline when its project is being moved into 契約(稼働中) without a contract
    that carries both period dates.

    Validated against the contract submitted in the SAME request (the inline forms) — not just the
    persisted contract — so filling the inline and flipping the phase in one save is allowed. The error
    is attached to the formset (non-form error) so Django highlights this 契約 component; the matching
    model-level gate is deferred on the admin change form (see ``KippoProject.clean``).
    """
    if any(formset.errors):  # per-row field errors already flag the inline; don't stack another error
        return
    project = formset.instance
    if project is None or not project._is_entering_under_contract():
        return

    def _effective_period_complete(form: Form) -> bool:
        """Whether the contract this form persists ends up with BOTH period dates after save.

        A form flagged for deletion removes the contract (no period). For a NEW contract a blank date is
        backfilled from the project (KippoProjectContract.save, creation only), so a blank period counts
        as complete when the project supplies both dates; on an edit a blank date is honored as-is.
        """
        cleaned = getattr(form, "cleaned_data", None)
        if not cleaned or formset._should_delete_form(form):
            return False
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if form.instance._state.adding:  # new contract -> a blank period backfills from the project on save
            start = start or project.start_date
            end = end or project.target_date
        return bool(start and end)

    # The inline is a OneToOne (max_num=1); its submitted rows ARE the post-save contract state — so a
    # same-request delete or period-clear is judged on the effective result, not the stale DB row. Only
    # when no contract row is submitted at all do we fall back to the persisted contract.
    contract_forms = [form for form in formset.forms if getattr(form, "cleaned_data", None)]
    if contract_forms:
        gate_satisfied = any(_effective_period_complete(form) for form in contract_forms)
    else:
        existing = project.get_contract()
        gate_satisfied = bool(existing and existing.has_complete_period())
    if not gate_satisfied:
        raise ValidationError(CONTRACT_REQUIRED_FOR_UNDER_CONTRACT_MSG)


class KippoProjectContractBillingEntryInline(KippoProjectBillingEntryInline):
    # Collapsible on the project page only; the standalone contract admin keeps the ledger expanded.
    classes = ("collapse",)


class KippoProjectContractInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, nested_admin.NestedStackedInline):
    model = KippoProjectContract
    classes = ("collapse",)
    extra = 1
    max_num = 1  # OneToOne — one contract per project (kippo#31)
    min_num = 0  # the contract is added on a later edit, not at registration (the inline is hidden on /add/)
    fields = ("billed_to", "billing_type", "pricing_basis", "total_amount", "estimated_monthly_amount", "start_date", "end_date", "note")
    # 請求先 (billed_to) uses the same searchable autocomplete as the project's 顧客 field instead of an
    # unbounded all-organizations <select>; get_formset scopes it to the project's organization.
    autocomplete_fields = ("billed_to",)
    inlines = (KippoProjectContractBillingEntryInline,)  # billing entries nested under the contract (django-nested-admin)

    def get_formset(self, request: DjangoRequest, obj: KippoProject | None = None, **kwargs):
        """Pre-fill the new contract row's period with the project's dates so the admin user
        sees the defaults before saving (the model still backfills them on save if cleared).
        An untouched pre-filled row is skipped by the formset, so it never creates a contract.

        Also enforces the 契約(稼働中) phase gate here (see ``clean``) so the requirement is surfaced on
        this contract component rather than on the parent's phase field.
        """
        # Stash the project's org BEFORE super() builds the form so formfield_for_foreignkey can pin the
        # 請求先 autocomplete dropdown to it. The inline is only rendered on the change form, so obj is set.
        request._billed_to_autocomplete_organization_id = obj.organization_id if obj is not None else None
        formset = super().get_formset(request, obj, **kwargs)
        # Scope 請求先 validation to the project's organization (parity with KippoProjectAdmin.
        # _scope_customer_queryset for 顧客): rejects a tampered cross-org submission even though the
        # dropdown is already narrowed. The inline is only rendered on the change form, so obj is set.
        if "billed_to" in formset.form.base_fields and obj is not None and obj.organization_id:
            formset.form.base_fields["billed_to"].queryset = KippoCustomer.objects.filter(organization=obj.organization)
        period_initial = [{"start_date": obj.start_date, "end_date": obj.target_date}] if obj and (obj.start_date or obj.target_date) else None

        class KippoProjectContractInlineFormSet(formset):
            def __init__(self, *args, **inner_kwargs) -> None:
                if period_initial is not None:
                    inner_kwargs.setdefault("initial", period_initial)
                super().__init__(*args, **inner_kwargs)

            def clean(self) -> None:
                super().clean()
                _validate_under_contract_gate(self)

        return KippoProjectContractInlineFormSet

    def formfield_for_foreignkey(self, db_field: models.ForeignKey, request: DjangoRequest, **kwargs):
        # Pin the 請求先 autocomplete dropdown to the project's organization (stashed in get_formset).
        if db_field.name == "billed_to" and "billed_to" in self.get_autocomplete_fields(request):
            kwargs["widget"] = _customer_autocomplete_widget(
                self, db_field, getattr(request, "_billed_to_autocomplete_organization_id", None), kwargs.get("using")
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class KippoMilestoneReadOnlyInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoMilestone
    extra = 0
    fields = ("title", "start_date", "target_date", "actual_date", "allocated_staff_days", "description")
    readonly_fields = ("title", "start_date", "target_date", "actual_date", "allocated_staff_days", "description")
    classes = ["collapse"]

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None):  # No Add button
        return False

    def get_queryset(self, request: DjangoRequest):
        # order milestones as expected
        qs = super().get_queryset(request).order_by("target_date")
        return qs


class KippoMilestoneAdminInline(AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoMilestone
    extra = 0
    fields = ("title", "start_date", "target_date", "actual_date", "allocated_staff_days", "description")
    classes = ["collapse"]

    def get_queryset(self, request: DjangoRequest):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


class ProjectWeeklyEffortReadOnlyInine(AllowIsStaffAdminMixin, nested_admin.NestedTabularInline):
    model = ProjectWeeklyEffort
    extra = 0
    fields = ("week_start", "user", "hours")
    readonly_fields = ("week_start", "user", "hours")
    classes = ["collapse"]

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None) -> bool:  # No Add button
        return False

    def get_queryset(self, request: DjangoRequest):
        # order milestones as expected
        three_weeks_ago = (timezone.now() - timezone.timedelta(days=21)).date()
        # filter output
        qs = super().get_queryset(request).filter(week_start__gte=three_weeks_ago).order_by("week_start")
        return qs


class ProjectWeeklyEffortInlineFormSet(BaseInlineFormSet):
    """Blocks non-superusers from adding effort for a closed week (kippo#33 / T17).

    `request_user` is set per-request by ProjectWeeklyEffortAdminInline.get_formset.
    """

    request_user = None

    def clean(self):
        super().clean()
        if self.request_user is None or self.request_user.is_superuser:
            return
        for form in self.forms:
            if not form.has_changed() or not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            week_start = form.cleaned_data.get("week_start")
            effort_user = form.cleaned_data.get("user")
            # guard organization_id: on the project add-form self.instance is unsaved and accessing
            # .organization directly would raise RelatedObjectDoesNotExist
            if not (week_start and effort_user and self.instance.organization_id):
                continue
            if self.instance.organization.is_weeklyeffort_closed(effort_user, week_start):
                form.add_error("week_start", WEEKLY_EFFORT_CLOSED_MESSAGE)


class ProjectWeeklyEffortAdminInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, nested_admin.NestedTabularInline):
    model = ProjectWeeklyEffort
    extra = 1
    fields = ("week_start", "user", "hours")
    formset = ProjectWeeklyEffortInlineFormSet

    def get_queryset(self, request: DjangoRequest):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs

    def get_formset(self, request: HttpRequest, obj: ProjectWeeklyEffort | None = None, **kwargs):
        """Filter the user selection list and restrict who an entry may be created for.

        Superusers may add effort for any member of the project's organization. Non-superusers
        may only add their own effort: the queryset is scoped to just the requesting user, so the
        field's normal ModelChoiceField validation rejects a tampered submission server-side.
        """
        formset = super().get_formset(request, obj, **kwargs)
        formset.request_user = request.user
        if obj:  # parent model
            formset.form.base_fields["user"].initial = request.user
            if request.user.is_superuser:
                # get users belonging to the organization this project belongs to
                related_organization_user_ids = OrganizationMembership.objects.filter(organization=obj.organization).values_list(
                    "user__id", flat=True
                )
                formset.form.base_fields["user"].queryset = KippoUser.objects.filter(id__in=related_organization_user_ids).order_by(
                    "last_name", "username"
                )
            else:
                formset.form.base_fields["user"].queryset = KippoUser.objects.filter(id=request.user.id)

        return formset


class KippoProjectPhaseConfidenceChangeInline(AllowIsStaffAdminMixin, nested_admin.NestedTabularInline):
    """Read-only フェーズ / 確度 change history (kippo#77), newest first, collapsed by default."""

    model = KippoProjectPhaseConfidenceChange
    extra = 0
    fields = ("changed_datetime", "changed_by", "phase_change", "confidence_change")
    readonly_fields = fields
    classes = ["collapse"]

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None) -> bool:
        return False

    def has_change_permission(self, request: DjangoRequest, obj: models.Model | None = None) -> bool:
        return False

    def has_delete_permission(self, request: DjangoRequest, obj: models.Model | None = None) -> bool:
        return False

    def get_queryset(self, request: DjangoRequest):
        return super().get_queryset(request).select_related("changed_by")

    @admin.display(description=_("フェーズ"))
    def phase_change(self, obj: KippoProjectPhaseConfidenceChange) -> str:
        if obj.previous_phase == obj.new_phase:
            return obj.get_new_phase_display()
        return f"{obj.get_previous_phase_display()} → {obj.get_new_phase_display()}"

    @admin.display(description=_("確度"))
    def confidence_change(self, obj: KippoProjectPhaseConfidenceChange) -> str:
        if obj.previous_confidence == obj.new_confidence:
            return f"{obj.new_confidence}%"
        return f"{obj.previous_confidence}% → {obj.new_confidence}%"


class KippoProjectStatusReadOnlyInine(AllowIsStaffAdminMixin, nested_admin.NestedTabularInline):
    model = KippoProjectStatus
    extra = 0
    fields = ("created_datetime", "created_by", "comment")
    readonly_fields = ("created_datetime", "created_by", "comment")
    classes = ["collapse"]

    def has_add_permission(self, request: DjangoRequest, obj: models.Model | None = None):  # No Add button
        return False

    def get_queryset(self, request: DjangoRequest):
        # order milestones as expected
        five_weeks_ago_days = 7 * 5
        five_weeks_ago = timezone.now() - timezone.timedelta(days=five_weeks_ago_days)
        qs = super().get_queryset(request).filter(created_datetime__gte=five_weeks_ago).order_by("created_datetime")
        return qs


class KippoProjectStatusAdminInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, nested_admin.NestedTabularInline):
    model = KippoProjectStatus
    extra = 1
    fields = ("comment",)

    def get_queryset(self, request: DjangoRequest):
        # clear the queryset so that no EDITABLE entries are displayed
        qs = super().get_queryset(request).none()
        return qs


class GithubRepositoryProjectInlineForm(forms.ModelForm):
    class Meta:
        model = GithubRepository
        fields = ("html_url",)

    def clean(self):
        cleaned = super().clean()
        html_url = (cleaned.get("html_url") or "").strip().rstrip("/")
        if not html_url:
            # Untouched extra rows must no-op so the parent project save isn't blocked.
            if self.has_changed():
                raise forms.ValidationError(_("GitHub repository URL is required."))
            return cleaned
        parsed = urllib.parse.urlparse(html_url)
        parts = [p for p in parsed.path.split("/") if p]
        github_url_min_path_segments = 2  # owner/repo
        if parsed.netloc != "github.com" or len(parts) < github_url_min_path_segments:
            raise forms.ValidationError(_("Invalid GitHub repository URL — expected https://github.com/owner/repo"))
        owner, repo = parts[0], parts[1]
        cleaned["html_url"] = f"https://github.com/{owner}/{repo}"
        cleaned["_derived_owner"] = owner
        cleaned["_derived_repo"] = repo
        return cleaned

    def save(self, commit: bool = True):
        instance = self.instance
        # BaseModelFormSet.save_m2m() iterates saved_forms calling form.save_m2m();
        # ModelForm.save() normally wires that up — this override bypasses it.
        self.save_m2m = self._save_m2m
        # organization is non-nullable; seed it from the parent project before any
        # GithubRepository.save() path runs (fixes monkut/kippo#266).
        if not instance.organization_id and instance.project_id:
            instance.organization = instance.project.organization

        owner = self.cleaned_data.get("_derived_owner")
        repo = self.cleaned_data.get("_derived_repo")
        if not owner or not repo:
            return super().save(commit=commit)

        normalized_html_url = f"https://github.com/{owner}/{repo}"
        api_url = f"https://api.github.com/repos/{owner}/{repo}"

        # GithubRepository.id is a UUIDField with default=uuid.uuid4, so instance.pk is
        # always truthy — use instance._state.adding to detect a brand-new row instead.
        if not instance._state.adding:
            instance.name = repo
            instance.html_url = normalized_html_url
            instance.api_url = api_url
            if commit:
                instance.save()
            return instance

        # New row: adopt an existing GithubRepository if one already matches the
        # unique_together (name, api_url, html_url) — avoids duplicating rows that
        # were auto-created by KippoTask.save().
        matched = GithubRepository.objects.filter(
            name=repo,
            api_url=api_url,
            html_url=normalized_html_url,
        ).first()
        if matched:
            matched.project = instance.project
            if commit:
                matched.save(update_fields=["project"])
            return matched

        instance.name = repo
        instance.html_url = normalized_html_url
        instance.api_url = api_url
        if commit:
            instance.save()
        return instance


class GithubRepositoryProjectInlineFormSet(BaseInlineFormSet):
    def delete_existing(self, obj: GithubRepository, commit: bool = True):
        # Unlink (clear FK) instead of delete: GithubMilestone references the
        # repository with on_delete=CASCADE, and KippoTask.save() looks up rows
        # by URL — destroying the row would cascade and break those flows.
        obj.project = None
        if commit:
            obj.save(update_fields=["project"])


class GithubRepositoryProjectInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, nested_admin.NestedStackedInline):
    model = GithubRepository
    fk_name = "project"
    form = GithubRepositoryProjectInlineForm
    formset = GithubRepositoryProjectInlineFormSet
    extra = 0
    max_num = 5
    fields = ("html_url",)
    classes = ("collapse",)
    verbose_name = _("Github Repository")
    verbose_name_plural = _("Github Repositories")
