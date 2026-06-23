import csv
import datetime
import json
import logging
import re
import urllib.parse
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from string import ascii_lowercase
from typing import TYPE_CHECKING

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.admin import AllowIsStaffAdminMixin, PrettyJSONWidget, UserCreatedBaseModelAdmin
from commons.definitions import SATURDAY
from commons.functions import get_current_month_date_range
from commons.widgets import MonthYearWidget
from customers.models import KippoCustomer
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, JSONField, Model, QuerySet, Value, When
from django.forms import BaseFormSet, Form
from django.forms.models import BaseInlineFormSet
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    request as DjangoRequest,  # noqa: N812
)
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from octocat.functions import copy_project_v2, create_project_v2, get_organization_id
from octocat.models import GithubRepository
from rangefilter.filters import DateRangeFilterBuilder
from slack_sdk.errors import SlackApiError
from tasks.models import KippoTaskStatus
from tasks.periodic.tasks import collect_github_project_issues

from .definitions import UPSELL_CATEGORY_VALUES, WEEKLY_EFFORT_CLOSED_MESSAGE, ProjectProgressStatus, ProjectRoles
from .exceptions import GithubMilestoneAlreadyExistsError, SlackChannelNotFoundError
from .functions import (
    generate_kippoprojectusermonthlystatisfaction_csv,
    generate_kippoprojectuserstatisfactionresult_csv,
    generate_projectmonthlyeffort_csv,
    generate_projectstatuscomments_csv,
    generate_projectweeklyeffort_csv,
    get_kippoproject_taskstatus_csv_rows,
    get_user_session_organization,
)
from .models import (
    ActiveKippoProject,
    CollectIssuesAction,
    KippoMilestone,
    KippoProject,
    KippoProjectBillingEntry,
    KippoProjectContract,
    KippoProjectOrganizationCategory,
    KippoProjectStatus,
    KippoProjectUserMonthlyStatisfactionResult,
    KippoProjectUserStatisfactionResult,
    ProjectAssignmentRate,
    ProjectColumn,
    ProjectColumnSet,
    ProjectMonthlyAssignment,
    ProjectMonthlyCost,
    ProjectWeeklyEffort,
    ProjectWeeklyEffortUnlock,
)

if TYPE_CHECKING:
    from .services.forecast import ForecastResult

CLOSE_PROJECT_NO_UPSELL_VALUE = "__no_upsell__"

# GET/POST param carrying the admin URL to return to after a project add/change. Set by callers
# that send the user into the project add form from elsewhere (e.g. the customer admin's
# "プロジェクトを追加" button) so the save redirects back to where they came from.
RETURN_TO_PARAM = "_return_to"

# Closure/survey fields, only meaningful once a project is closed. Named once so the all-projects
# admin (hides them on /add/) and the active admin (hides them always — active ⇒ never closed)
# stay in sync instead of repeating the literal tuple.
PROJECT_CLOSURE_FIELDS = ("close_comment", "survey_issued")

# Label of the read-only revenue summary fieldset. A constant so the base admin and the active
# admin (which hides it — billing is managed on KippoProjectContractAdmin) reference the same object.
BILLING_FIELDSET_LABEL = _("Billing")

logger = logging.getLogger(__name__)

# Default per-role daily rates pre-filled into the ProjectAssignmentRate inline on /add/.
# Edit the values in fixtures/default_projectassignmentrates.json — no code change needed.
# Keep the roles in this file aligned with ProjectRoles (rows with an unknown role are skipped).
DEFAULT_ASSIGNMENT_RATES_FIXTURE = Path(__file__).parent / "fixtures" / "default_projectassignmentrates.json"


def _default_assignment_rate_initial() -> tuple[dict, ...]:
    """Role/rate_per_day defaults used to prefill the ProjectAssignmentRate inline on project creation.

    Read fresh each call (tiny file, only on /add/). Rows whose role is not a valid ProjectRoles
    value are skipped (logged); a missing/blank rate falls back to settings.DEFAULT_PROJECT_DAILY_RATE
    so the fixture and the model default cannot silently drift.
    """
    try:
        rows = json.loads(DEFAULT_ASSIGNMENT_RATES_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Failed to load default project assignment rates fixture: %s", DEFAULT_ASSIGNMENT_RATES_FIXTURE)
        return ()
    valid_roles = set(ProjectRoles.values())
    initial = []
    for row in rows:
        role = row.get("role")
        if role not in valid_roles:
            logger.warning("Skipping default assignment rate with unknown role %r (valid roles: %s)", role, sorted(valid_roles))
            continue
        initial.append({"role": role, "rate_per_day": row.get("rate_per_day") or settings.DEFAULT_PROJECT_DAILY_RATE})
    return tuple(initial)


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


class ProjectAssignmentRateInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, admin.TabularInline):
    model = ProjectAssignmentRate
    extra = 0
    # One rate per role (unique_together project+role) — cap rows at the number of defined roles so
    # no extra entries can be added beyond them. Stays in sync if ProjectRoles changes.
    max_num = len(ProjectRoles.choices())
    fields = ("role", "rate_per_day")

    def get_formset(self, request: DjangoRequest, obj: KippoProject | None = None, **kwargs):
        """On /add/ (obj is None) prefill one row per default role/rate from the fixture so the
        section is populated with sensible defaults the admin can edit before saving.
        """
        formset = super().get_formset(request, obj, **kwargs)
        if obj is not None:
            return formset
        defaults = _default_assignment_rate_initial()
        if not defaults:
            return formset
        formset.extra = len(defaults)

        class DefaultRatesFormSet(formset):
            def __init__(self, *args, **inner_kwargs) -> None:
                inner_kwargs.setdefault("initial", list(defaults))
                super().__init__(*args, **inner_kwargs)

        return DefaultRatesFormSet


class ProjectMonthlyAssignmentInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, admin.TabularInline):
    model = ProjectMonthlyAssignment
    extra = 0
    fields = ("user", "month", "percentage", "is_confirmed")
    classes = ["collapse"]


class KippoProjectContractInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, admin.TabularInline):
    model = KippoProjectContract
    extra = 1
    max_num = 1  # OneToOne — one contract per project (kippo#31)
    fields = ("billing_type", "total_amount", "start_date", "end_date", "note")

    def get_min_num(self, request: DjangoRequest, obj: KippoProject | None = None, **kwargs):
        # 請求方法 is required at project registration (kippo#40 / T19): require the contract on /add/.
        # Editing an existing project is unaffected (existing contract-less projects still save).
        return 1 if obj is None else 0

    def get_formset(self, request: DjangoRequest, obj: KippoProject | None = None, **kwargs):
        """Pre-fill the new contract row's period with the project's dates so the admin user
        sees the defaults before saving (the model still backfills them on save if cleared).
        An untouched pre-filled row is skipped by the formset, so it never creates a contract.
        """
        formset = super().get_formset(request, obj, **kwargs)
        if obj is None or not (obj.start_date or obj.target_date):
            return formset
        period_initial = [{"start_date": obj.start_date, "end_date": obj.target_date}]

        class PeriodPrefilledFormSet(formset):
            def __init__(self, *args, **inner_kwargs) -> None:
                inner_kwargs.setdefault("initial", period_initial)
                super().__init__(*args, **inner_kwargs)

        return PeriodPrefilledFormSet


class KippoProjectBillingEntryInline(AllowIsStaffAdminMixin, admin.TabularInline):
    # Billing entries belong to the contract (kippo#31): edited via KippoProjectContractAdmin, not
    # the project (the entry no longer carries a project FK).
    model = KippoProjectBillingEntry
    extra = 0
    fields = ("billing_date", "amount", "is_manual", "note")


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


class ProjectWeeklyEffortReadOnlyInine(AllowIsStaffAdminMixin, admin.TabularInline):
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


class ProjectWeeklyEffortAdminInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, admin.TabularInline):
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


class KippoProjectStatusReadOnlyInine(AllowIsStaffAdminMixin, admin.TabularInline):
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


class KippoProjectStatusAdminInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, admin.TabularInline):
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


class GithubRepositoryProjectInline(LockWhenProjectClosedInlineMixin, AllowIsStaffAdminMixin, admin.StackedInline):
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


def create_github_organizational_project_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:
    """
    Admin Action command to create a GitHub organizational project (ProjectsV2) from the selected KippoProject(s).

    Uses the GitHub ProjectsV2 API. If the organization has a default_github_project_template configured,
    the new project will be created by copying that template. Otherwise, a blank project is created.
    """
    successful_creation_projects = []
    skipping = []
    errors = []
    created_without_template = []
    for kippo_project in queryset:
        if kippo_project.github_project_html_url:
            message = f"{kippo_project.name} already has GitHub Project set ({kippo_project.github_project_html_url}), SKIPPING!"
            logger.warning(message)
            skipping.append(message)
            continue

        try:
            github_organization_name = kippo_project.organization.github_organization_name
            githubaccesstoken = kippo_project.organization.githubaccesstoken
            token = githubaccesstoken.token

            # Get organization node ID required for ProjectsV2 mutations
            org_id = get_organization_id(github_organization_name, token)
            logger.debug(f"Organization ID for {github_organization_name}: {org_id}")

            # Use the project name for the GitHub project title
            project_title = kippo_project.name

            # Check if a template is configured
            template_id = kippo_project.organization.default_github_project_template
            if template_id:
                # Create project by copying the template
                logger.info(f"Creating ProjectsV2 from template {template_id} for {kippo_project.name}")
                project_data = copy_project_v2(template_id, org_id, project_title, token)
            else:
                # Create a blank project
                logger.warning(f"No template configured for {github_organization_name}, creating blank ProjectsV2 for {kippo_project.name}")
                project_data = create_project_v2(org_id, project_title, token)
                created_without_template.append(kippo_project.name)

            # Update KippoProject with the new project URLs
            kippo_project.github_project_html_url = project_data["url"]
            # ProjectsV2 uses GraphQL node IDs instead of REST API URLs
            kippo_project.github_project_api_nodeid = project_data["id"]
            kippo_project.save()

            logger.info(f"Created GitHub ProjectsV2: {project_data['url']}")
            successful_creation_projects.append((kippo_project.name, project_data["url"]))

        except Exception as e:
            error_msg = f"Failed to create GitHub Project for {kippo_project.name}: {e}"
            logger.exception(error_msg)
            errors.append(error_msg)

    if skipping:
        for m in skipping:
            modeladmin.message_user(request, message=m, level=messages.WARNING)
    if created_without_template:
        modeladmin.message_user(
            request,
            message=f"No default_github_project_template configured for organization. Empty project(s) created: {created_without_template}",
            level=messages.WARNING,
        )
    if errors:
        for m in errors:
            modeladmin.message_user(request, message=m, level=messages.ERROR)
    if successful_creation_projects:
        project_links = format_html_join(
            ", ",
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            ((url, name) for name, url in successful_creation_projects),
        )
        modeladmin.message_user(
            request,
            message=format_html("({}) GitHub Projects Created: {}", len(successful_creation_projects), project_links),
            level=messages.INFO,
        )


create_github_organizational_project_action.short_description = _("Create Github Organizational Project(s) for selected")  # noqa: E305


def create_github_repository_milestones_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:
    """
    Admin Action command to create a github repository milestones for ALL
    repositories linked to the selected KippoProject(s).
    """
    for kippo_project in queryset:
        milestones = kippo_project.active_milestones()
        for milestone in milestones:
            try:
                created_octocat_milestones = milestone.update_github_milestones(request.user)
                for _, created_octocat_milestone in created_octocat_milestones:
                    modeladmin.message_user(
                        request,
                        message=f"({kippo_project.name}) {created_octocat_milestone.repository.name} created milestone: "
                        f"{milestone.title} ({milestone.start_date} - {milestone.target_date})",
                        level=messages.INFO,
                    )
            except GithubMilestoneAlreadyExistsError as e:
                modeladmin.message_user(
                    request,
                    message=f"({kippo_project.name}) Failed to create milestone for related repository(ies): {e.args}",
                    level=messages.ERROR,
                )


create_github_repository_milestones_action.short_description = _("Create related Github Repository Milestone(s) for selected")  # noqa: E305


def collect_project_github_repositories_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:
    """
    Admin action to collect the github repositories for selected KippoProjects
    Calls `()` which also updates issues on collection
    """
    # get request user organization
    organization, user_organizations = get_user_session_organization(request)

    # collect project github html_urls to filter for the collect_github_project_issues functoin
    github_project_html_urls_to_update = []
    for kippoproject in queryset.filter(organization__in=user_organizations):  # apply filter to only access user accessible orgs
        logger.debug(f"adding project: {kippoproject}")
        github_project_html_urls_to_update.append(kippoproject.github_project_html_url)

    collect_github_project_issues(1, kippo_organization_id=str(organization.id), github_project_html_urls=github_project_html_urls_to_update)
    modeladmin.message_user(
        request,
        message=f"({len(github_project_html_urls_to_update)}) KippoProjects updated from GitHub Organizational Projects",
        level=messages.INFO,
    )


collect_project_github_repositories_action.short_description = _("Collect Project Repositories")  # noqa: E305


class CloseProjectActionForm(forms.Form):
    CATEGORY_CHOICES = (
        ("upsell-improvement", _("(Upsell) 追加改善・拡張")),
        ("upsell-new-proposal", _("(Upsell) 新規提案")),
        ("upsell-new-department", _("(Upsell) 別部署紹介")),
        (CLOSE_PROJECT_NO_UPSELL_VALUE, _("upsellなし")),
    )

    category = forms.ChoiceField(
        label=_("Category"),
        widget=forms.Select,
        choices=CATEGORY_CHOICES,
        initial=CLOSE_PROJECT_NO_UPSELL_VALUE,
    )
    close_comment = forms.CharField(
        label=_("Close Comment"),
        widget=forms.Textarea(attrs={"rows": 4, "cols": 60}),
        required=False,
    )

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        close_comment = cleaned_data.get("close_comment", "").strip()
        if category == CLOSE_PROJECT_NO_UPSELL_VALUE and not close_comment:
            raise ValidationError({"close_comment": _("Close Comment is required when upsellなし is selected.")})
        return cleaned_data


def _next_upsell_project_name(name: str) -> str:
    match = re.match(r"(.*) Phase (\d+)$", name)
    if match:
        return f"{match.group(1)} Phase {int(match.group(2)) + 1}"
    return f"{name} Phase 2"


def _start_of_next_month(today: datetime.date) -> datetime.date:
    return (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)


def _build_upsell_prefill_params(project: KippoProject, selected_category: str) -> dict[str, str]:
    today = timezone.now().date()
    # category is now a FK — the add-form prefill needs the global category's PK, not its key string.
    upsell_category_pk = (
        KippoProjectOrganizationCategory.objects.filter(organization__isnull=True, key=selected_category).values_list("pk", flat=True).first()
    )
    params = {
        "category": str(upsell_category_pk) if upsell_category_pk else "",
        "parent_project": str(project.id),
        "organization": str(project.organization_id),
        "_upsell_source": "close",
        "name": _next_upsell_project_name(project.name),
        "start_date": _start_of_next_month(today).isoformat(),
        "slack_channel_name": project.slack_channel_name,
        "slack_notification_channel_name": project.slack_notification_channel_name,
        "document_folder_url": project.document_folder_url,
        "github_project_html_url": project.github_project_html_url,
        "github_project_api_nodeid": project.github_project_api_nodeid,
        "docbase_tag": ",".join(project.docbase_tag),  # type: ignore[arg-type]
    }
    if project.columnset_id:
        params["columnset"] = str(project.columnset_id)
    return {k: v for k, v in params.items() if v}


def close_kippoproject_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet):
    """Close a KippoProject with an optional upsell follow-up project."""
    if queryset.count() != 1:
        modeladmin.message_user(request, _("Select exactly one project to close."), level=messages.ERROR)
        return None

    project: KippoProject = queryset.first()
    if project.is_closed:
        modeladmin.message_user(
            request,
            _("Project is already closed; re-open the project before closing again."),
            level=messages.ERROR,
        )
        return None

    if request.POST.get("post") == "yes":
        form = CloseProjectActionForm(request.POST)
        if form.is_valid():
            selected_category = form.cleaned_data["category"]
            project.close_comment = form.cleaned_data["close_comment"]
            project.is_closed = True
            project.actual_date = timezone.now().date()
            project.display_as_active = False
            project.display_in_project_report = False
            project.updated_by = request.user
            project.save()

            modeladmin.message_user(request, _("Project '%s' closed.") % project.name, level=messages.INFO)

            if selected_category in UPSELL_CATEGORY_VALUES:
                params = urllib.parse.urlencode(_build_upsell_prefill_params(project, selected_category))
                return HttpResponseRedirect(f"{settings.URL_PREFIX}/admin/projects/kippoproject/add/?{params}")
            return HttpResponseRedirect(f"{settings.URL_PREFIX}/admin/projects/kippoproject/")
    else:
        form = CloseProjectActionForm()

    context = {
        **modeladmin.admin_site.each_context(request),
        "title": _("Close Project"),
        "project": project,
        "form": form,
        "action": "close_kippoproject_action",
        "opts": modeladmin.model._meta,
        "no_upsell_value": CLOSE_PROJECT_NO_UPSELL_VALUE,
    }
    return TemplateResponse(request, "admin/projects/close_project_action.html", context)


close_kippoproject_action.short_description = _("Close Project")  # noqa: E305


def reopen_kippoproject_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet):
    """Re-open one or more closed KippoProjects and record a status comment."""
    selected = list(queryset)
    closed_projects = [p for p in selected if p.is_closed]
    skipped_count = len(selected) - len(closed_projects)
    if skipped_count:
        modeladmin.message_user(
            request,
            _("%(count)d project(s) were not closed and were skipped.") % {"count": skipped_count},
            level=messages.WARNING,
        )
    if not closed_projects:
        modeladmin.message_user(request, _("No closed projects selected to re-open."), level=messages.ERROR)
        return

    reopen_comment = _("re-opened by %(username)s") % {"username": request.user.username}
    for project in closed_projects:
        project.is_closed = False
        project.actual_date = None
        project.updated_by = request.user
        project.save()
        KippoProjectStatus.objects.create(
            project=project,
            comment=reopen_comment,
            created_by=request.user,
            updated_by=request.user,
        )
    modeladmin.message_user(
        request,
        _("Re-opened %(count)d project(s).") % {"count": len(closed_projects)},
        level=messages.INFO,
    )


reopen_kippoproject_action.short_description = _("Re-open Project(s)")  # noqa: E305


def add_calendar_links_to_slack_channels_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet):
    """Add each selected project's MTG calendar-template URL as a pinned message on its Slack conversation channel.

    Skips projects without a Slack conversation channel (or whose organization has no Slack API token)
    and reports them as errors. See kiconiaworks/kippo#13.
    """
    from .slackcommand.managers import ProjectCalendarLinkManager

    added: list[str] = []
    updated: list[str] = []
    errors: list[str] = []
    managers: dict = {}
    for project in queryset.select_related("organization"):
        if not project.slack_channel_name:
            errors.append(_("%(name)s: no Slack conversation channel is configured.") % {"name": project.name})
            continue
        organization = project.organization
        if not organization.slack_api_token:
            errors.append(_("%(name)s: organization '%(org)s' has no Slack API token configured.") % {"name": project.name, "org": organization.name})
            continue
        manager = managers.get(organization.id)
        if manager is None:
            manager = ProjectCalendarLinkManager(organization)
            managers[organization.id] = manager
        try:
            result = manager.set_calendar_pinned_message(project)
        except SlackChannelNotFoundError:
            errors.append(
                _("%(name)s: Slack channel '%(channel)s' was not found in the workspace.")
                % {"name": project.name, "channel": project.slack_channel_name}
            )
        except SlackApiError as e:
            errors.append(_("%(name)s: Slack API error — %(error)s") % {"name": project.name, "error": e.response["error"]})
        else:
            (added if result == "added" else updated).append(project.name)

    if added:
        modeladmin.message_user(request, _("Added calendar link to: %s") % ", ".join(added), level=messages.INFO)
    if updated:
        modeladmin.message_user(request, _("Updated calendar link for: %s") % ", ".join(updated), level=messages.INFO)
    for error in errors:
        modeladmin.message_user(request, error, level=messages.ERROR)


add_calendar_links_to_slack_channels_action.short_description = _("Add MTG calendar link to Slack channel")  # noqa: E305


class KippoProjectAdminForm(forms.ModelForm):
    # Required at project registration (kippo#40 / T19) — enforced create-only so existing rows/edits
    # are unaffected. category/phase always carry model defaults; 請求方法 is enforced via the required
    # contract inline (see KippoProjectContractInline.get_min_num).
    REQUIRED_AT_REGISTRATION = ("customer", "project_manager", "start_date", "target_date")

    class Meta:
        model = KippoProject
        exclude = ()  # noqa: DJ006 (admin form inherits field config from ModelAdmin)

    def clean(self):
        cleaned_data = super().clean()
        # KippoProject.id is a UUID with a default, so .pk is set even before save — use _state.adding
        # to detect a genuine registration (add) vs an edit of an existing row.
        if self.instance._state.adding:
            for field in self.REQUIRED_AT_REGISTRATION:
                if not cleaned_data.get(field):
                    self.add_error(field, _("This field is required at project registration."))
        category = cleaned_data.get("category")
        organization = cleaned_data.get("organization")
        submitted_parent_project = cleaned_data.get("parent_project")
        # parent_project is readonly on the change form, so it won't appear in cleaned_data there
        # — fall back to the persisted instance value so existing upsell projects keep validating.
        parent_project = submitted_parent_project or getattr(self.instance, "parent_project", None)
        if getattr(category, "key", None) in UPSELL_CATEGORY_VALUES and not parent_project:
            self.add_error(
                "parent_project",
                _("Parent Project is required when an upsell category is selected."),
            )
        # On /add/, parent_project must belong to the same organization as the new project.
        # (On /change/, parent_project is readonly so it isn't in submitted data — skip this check.)
        if submitted_parent_project and organization and submitted_parent_project.organization_id != organization.id:
            self.add_error(
                "parent_project",
                _("Parent Project must belong to the same organization as this project."),
            )
        return cleaned_data


def _format_estimated_completion(result: "ForecastResult") -> str:
    """Render the forecast result as a one-line admin display string."""
    date = result.estimated_completion_date
    if date is None:
        return str(_("(not estimable — no future assignments or insufficient data)"))
    delta = result.delta_from_target_date_days
    if delta is None:
        return date.isoformat()
    if delta == 0:
        return f"{date.isoformat()} (on target)"
    if delta > 0:
        return f"{date.isoformat()} ({delta} days behind target)"
    return f"{date.isoformat()} ({-delta} days ahead of target)"


@admin.register(KippoProjectOrganizationCategory)
class KippoProjectOrganizationCategoryAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = ("key", "label", "organization", "sort_order", "is_active")
    list_filter = ("is_active", "organization")
    search_fields = ("key", "label", "organization__name")
    ordering = ("organization", "sort_order", "key")


class KippoProjectBaseAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    """Shared config for the two registered project admins. Not registered itself.

    Subclassed by KippoProjectAdmin (all projects) and ActiveKippoProjectAdmin (active only, via
    the proxy's ActiveKippoProjectManager). Both render the same columns/ordering/actions; the
    children only differ in queryset and a couple of list/exclude tweaks.
    """

    form = KippoProjectAdminForm
    # Inlines hidden on /add/ (only meaningful once a project exists). Exposed as a class
    # attribute so tests and subclasses can reference the same source of truth.
    HIDDEN_ON_ADD_INLINES = (
        GithubRepositoryProjectInline,
        ProjectWeeklyEffortReadOnlyInine,
        ProjectWeeklyEffortAdminInline,
        KippoProjectStatusReadOnlyInine,
        KippoProjectStatusAdminInline,
    )
    # Shared base columns. KippoProjectAdmin appends display_as_active (it lists closed/inactive
    # projects too); the active admin uses this set as-is.
    list_display = (
        "id",
        "get_customer_name",
        "name",
        "get_problem_definition_display",
        "get_confidence_display",
        "get_projectstatus_display",
        "get_latest_kippoprojectstatus_comment",
        "start_date",
        "target_date",
        "get_kippoprojectuserstatisfactionresult_usernames",
        "get_projectsurvey_display_url",
        "show_github_project_html_url",
    )
    list_display_links = ("id", "name")
    list_select_related = ("customer", "category")
    search_fields = ("id", "name", "phase", "category__key", "category__label", "problem_definition", "customer__name")
    # 顧客 (customer) is selected via a searchable autocomplete (searches/displays KippoCustomer.name
    # through KippoCustomerAdmin.search_fields) instead of a long unsearchable <select>.
    autocomplete_fields = ("customer",)
    # revenue figures are derived from the contract + billing ledger (kippo#32 / T13) — shown read-only.
    # confidence is derived from phase (editable=False) and intentionally hidden from the form.
    readonly_fields = ("get_contract_amount_display", "get_total_revenue_display")
    # Changelist ordering lives in get_ordering() (a Case() expression), not the `ordering`
    # attribute — see the note there for why the attribute can't express it.
    actions = [
        create_github_organizational_project_action,
        create_github_repository_milestones_action,
        collect_project_github_repositories_action,
        close_kippoproject_action,
        reopen_kippoproject_action,
        add_calendar_links_to_slack_channels_action,
        "export_project_kippotaskstatus_csv",
        "export_kippoprojectstatus_comments_csv",
        "generate_billing_entries",
    ]
    exclude = ("is_closed", "actual_date", "display_as_active", "display_in_project_report")
    fieldsets = [
        (
            None,
            {
                "fields": (
                    "name",
                    "problem_definition",
                    "phase",
                    "project_manager",
                    "meeting_calendar_url_field",
                    "meeting_description_tag_field",
                )
            },
        ),
        (
            _("Dates & Estimates"),
            {
                "fields": (
                    "start_date",
                    "target_date",
                    "allocated_staff_days",
                    "estimated_completion_date",
                ),
            },
        ),
        (
            BILLING_FIELDSET_LABEL,
            {
                "classes": ("collapse",),
                "fields": ("get_contract_amount_display", "get_total_revenue_display"),
            },
        ),
        (
            _("Details"),
            {
                "classes": ("collapse",),
                "fields": (
                    "organization",
                    "customer",
                    "category",
                    "parent_project",
                    "slack_channel_name",
                    "slack_notification_channel_name",
                    "columnset",
                    "enable_cost_report",
                    "document_folder_url",
                    "github_project_html_url",
                    "github_project_api_nodeid",
                    "docbase_tag",
                ),
            },
        ),
    ]
    inlines = [
        # Milestones not used atm, commenting out.
        # KippoMilestoneReadOnlyInline,
        # KippoMilestoneAdminInline,
        ProjectAssignmentRateInline,
        ProjectMonthlyAssignmentInline,
        KippoProjectContractInline,
        GithubRepositoryProjectInline,
        ProjectWeeklyEffortReadOnlyInine,
        KippoProjectStatusReadOnlyInine,
        ProjectWeeklyEffortAdminInline,
        KippoProjectStatusAdminInline,
    ]
    # copy-to-clipboard handler + toast for the MTG calendar link readonly fields (kippo#13)
    # lives in templates/admin/projects/kippoproject/change_form.html
    change_form_template = "admin/projects/kippoproject/change_form.html"

    def has_add_permission(self, request: DjangoRequest, obj: KippoProject | None = None):  # No Add button
        # check if user has organization memberships
        # - if not can't add new projects
        return request.user.memberships.exists()

    def has_delete_permission(self, request: DjangoRequest, obj: Model | None = None):
        # Remove the delete button from the change page (bulk delete from the changelist stays).
        if "/change/" in request.path:
            return False
        return super().has_delete_permission(request, obj)

    def get_inlines(self, request: DjangoRequest, obj: KippoProject | None = None):
        inlines = list(super().get_inlines(request, obj))
        if obj is None:
            inlines = [cls for cls in inlines if cls not in self.HIDDEN_ON_ADD_INLINES]
        return inlines

    def get_exclude(self, request: DjangoRequest, obj: KippoProject | None = None):
        excluded = list(super().get_exclude(request, obj) or ())
        if obj is None:
            # MTG calendar links + survey/close fields are only meaningful once a project exists
            for fieldname in (*PROJECT_CLOSURE_FIELDS, "meeting_calendar_url_field", "meeting_description_tag_field"):
                if fieldname not in excluded:
                    excluded.append(fieldname)
        if not request.user.is_superuser and "github_project_api_nodeid" not in excluded:
            excluded.append("github_project_api_nodeid")
        # columnset is never an admin choice — it is auto-assigned to the organization's default
        # in save_model (see below). Hidden from the form for every user.
        if "columnset" not in excluded:
            excluded.append("columnset")
        return tuple(excluded)

    def get_fieldsets(self, request: DjangoRequest, obj: KippoProject | None = None):
        fieldsets = super().get_fieldsets(request, obj)
        excluded: set[str] = set(self.get_exclude(request, obj) or ())
        # estimated_completion_date is a computed readonly field, only surfaced for open projects on edit
        if obj is None or obj.is_closed:
            excluded.add("estimated_completion_date")
        # Build a fresh fieldset list (never mutate the class attribute): strip excluded fields and,
        # on /add/, expand the collapsed sections (Billing & Details) so their fields are visible
        # without an extra click.
        rebuilt = []
        for label, opts in fieldsets:
            new_opts = {**opts, "fields": tuple(f for f in opts.get("fields", ()) if f not in excluded)}
            if obj is None and "collapse" in new_opts.get("classes", ()):
                new_opts["classes"] = tuple(c for c in new_opts["classes"] if c != "collapse")
            rebuilt.append((label, new_opts))
        return rebuilt

    def get_updated_by_display(self, obj: KippoProject) -> str:
        result = ""
        if obj:
            result = obj.updated_by.username
        return result

    get_updated_by_display.short_description = "updated by"

    @admin.display(description=KippoCustomer._meta.verbose_name, ordering="customer__name")
    def get_customer_name(self, obj: KippoProject) -> str:
        return obj.customer.name if obj.customer else ""

    @admin.display(description=_("プロジェクト課題定義"))
    def get_problem_definition_display(self, obj: KippoProject) -> str:
        # truncated problem definition shown as the project intro in the changelist (kippo#29 / T07)
        limit = 60
        text = obj.problem_definition or ""
        return f"{text[:limit]}…" if len(text) > limit else text

    @admin.display(description=_("カテゴリ"), ordering="category__sort_order")
    def get_category_label(self, obj: KippoProject) -> str:
        return obj.category.label if obj.category_id else ""

    def formfield_for_foreignkey(self, db_field: models.Field, request: DjangoRequest, **kwargs):
        # Limit the category select to active categories (global defaults + any organization's custom categories).
        if db_field.name == "category":
            kwargs["queryset"] = KippoProjectOrganizationCategory.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description=_("confidence"), ordering="confidence")
    def get_confidence_display(self, obj: KippoProject):
        result = ""
        if obj.confidence:
            result = f"{obj.confidence} %"
        return result

    @admin.display(description=_("アンケート完了ユーザ"))
    def get_kippoprojectuserstatisfactionresult_usernames(self, obj: KippoProject | None = None) -> str:
        result = ""
        if obj:
            result = format_html(
                "<br>".join(
                    KippoProjectUserStatisfactionResult.objects.filter(project=obj)
                    .order_by("created_by__username")
                    .values_list("created_by__username", flat=True)
                )
            )
        return result

    @admin.display(description=_("顧客アンケートURL"))
    def get_projectsurvey_display_url(self, obj: KippoProject) -> str:
        url = obj.get_projectsurvey_url()
        html_encoded_url = ""
        if url:
            html_encoded_url = format_html(f"<a href='{url}'>Survey URL</a>")
        return html_encoded_url

    def export_project_kippotaskstatus_csv(self, request: DjangoRequest, queryset: models.QuerySet):
        """Allow export to csv from admin"""
        if queryset.count() != 1:
            self.message_user(request, _("CSV Export action only supports single Project selection"), level=messages.ERROR)
            return None
        project = queryset[0]
        logger.debug(f"Generating KippoTaskStatus CSV for: {project.name}")
        project_slug = "".join(c for c in project.name.replace(" ", "").lower() if c in ascii_lowercase)
        if not project_slug:
            project_slug = project.id
        filename = f"{project_slug}_{timezone.now().strftime('%Y%m%d_%H%M%Z')}.csv"
        logger.debug(f"filename: {filename}")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f"attachment; filename={filename}"
        writer = csv.writer(response)
        try:
            csv_row_generator = get_kippoproject_taskstatus_csv_rows(project, with_headers=True)
            writer.writerows(csv_row_generator)
        except KippoTaskStatus.DoesNotExist:
            display_message = _("No status entries exist for project: %s") % project.name
            self.message_user(request, display_message, level=messages.WARNING)
            return None
        return response

    export_project_kippotaskstatus_csv.short_description = _("Export KippoTaskStatus CSV")

    def export_kippoprojectstatus_comments_csv(self, request: DjangoRequest, queryset: models.QuerySet):
        project_ids = [str(i) for i in queryset.values_list("id", flat=True)]
        if project_ids:
            # initiate creation
            now = timezone.now()
            filename = now.strftime("project-statuscomments-%Y%m%d%H%M%S.csv")
            key = f"tmp/download/{filename}"
            generate_projectstatuscomments_csv(project_ids=project_ids, key=key)
            # redirect to waiter
            urlencoded_key = urllib.parse.quote_plus(key)
            backpath_urlencoded_key = urllib.parse.quote_plus(f"{settings.URL_PREFIX}/admin/projects/kippoproject/")
            download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}&back_path={backpath_urlencoded_key}"
            return HttpResponseRedirect(redirect_to=download_waiter_url)
        self.message_user(request, _("No Projects selected!"), level=messages.ERROR)
        return None

    export_kippoprojectstatus_comments_csv.description = _("Download Project Comments CSV")

    @admin.action(description=_("契約から請求エントリを生成"))
    def generate_billing_entries(self, request: DjangoRequest, queryset: models.QuerySet):
        created_count = 0
        skipped_names = []
        for project in queryset:
            contract = project.get_contract()
            project_created_count = len(contract.generate_billing_entries(created_by=request.user)) if contract else 0
            created_count += project_created_count
            if not project_created_count:
                skipped_names.append(project.name)
        if created_count:
            self.message_user(request, _("%d billing entries created.") % created_count, level=messages.INFO)
        if skipped_names:
            self.message_user(
                request,
                _("No entries created for: %s (no contract, contract dates unresolved, or already generated)") % ", ".join(skipped_names),
                level=messages.WARNING,
            )

    @admin.display(description=_("契約金額"))
    def get_contract_amount_display(self, obj: KippoProject | None = None) -> str:
        # derived from the project's contract (kippo#32 / T13)
        if obj is None or not obj.pk:
            return "-"
        return f"¥{obj.contract_amount:,.0f}"

    @admin.display(description=_("トータル売上"))
    def get_total_revenue_display(self, obj: KippoProject | None = None) -> str:
        # derived from the billing ledger (kippo#32 / T13)
        if obj is None or not obj.pk:
            return "-"
        return f"¥{obj.total_revenue:,.0f}"

    @admin.display(description=_("最新コメント"))
    def get_latest_kippoprojectstatus_comment(self, obj: KippoProject):
        result = ""
        latest_status = obj.get_latest_kippoprojectstatus()
        if latest_status:
            display_date = latest_status.created_datetime.strftime("(%m/%d) ")
            result = latest_status.comment
            spaces = "&nbsp;" * 75
            result = format_html("{display_date}{result}<br/>" + spaces, display_date=display_date, result=result)
        return result

    @admin.display(description=_("稼働状況"))
    def get_projectstatus_display(self, obj: KippoProject | None = None) -> str:
        progress_status_display = "-"
        if obj:
            progress_status_display = None
            project_progress_status: ProjectProgressStatus = obj.get_projectprogressstatus_values()
            # low < high
            # if x > high, then display as "red"
            # if x > low, then display as "yellow"
            # x <= low, then display as "green"
            logger.debug(f"project_progress_status.allocated_effort_hours={project_progress_status.allocated_effort_hours}")
            logger.debug(f"project_progress_status.expected_effort_hours={project_progress_status.expected_effort_hours}")
            if project_progress_status.allocated_effort_hours and project_progress_status.expected_effort_hours:
                low = int(project_progress_status.expected_effort_hours) + 1
                # percent_value = project_progress_status.allocated_effort_hours * (settings.PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD / 100)
                high = (
                    settings.PROJECT_STATUS_REPORT_EXCEEDING_THRESHOLD / 100
                ) * project_progress_status.expected_effort_hours + project_progress_status.expected_effort_hours

                max_value = project_progress_status.allocated_effort_hours
                if (
                    project_progress_status.allocated_effort_hours
                    and project_progress_status.current_effort_hours
                    and project_progress_status.allocated_effort_hours < project_progress_status.current_effort_hours
                ):
                    max_value = project_progress_status.current_effort_hours

                difference_percentage = project_progress_status.get_difference_percentage()
                if difference_percentage:
                    difference_percentage_display = f"+{int(difference_percentage)}" if difference_percentage > 0 else f"{int(difference_percentage)}"
                    low_display = f'low="{low}" ' if low < max_value else ""
                    progress_status_display = (
                        f"{project_progress_status.current_effort_hours}h<br/>"
                        f"{difference_percentage_display}%<br/>"
                        f'<meter min="0" '
                        f"{low_display}"
                        f'optimum="{int(project_progress_status.expected_effort_hours)}" '
                        f'high="{high}" '
                        f'max="{max_value}" '
                        f'value="{project_progress_status.current_effort_hours}"></meter>'
                    )
            if not progress_status_display and project_progress_status.current_effort_hours:
                progress_status_display = f"{project_progress_status.current_effort_hours}h"
            elif not progress_status_display:
                progress_status_display = "-"

            # Wrap in link to project status details page
            status_url = f"{settings.URL_PREFIX}/projects/project/{obj.id}/status/"
            progress_status_display = f'<a href="{status_url}">{progress_status_display}</a>'

        return mark_safe(progress_status_display)  # noqa: S308

    @admin.display(description=_("稼働時間"))
    def get_projecteffort_display(self, obj: KippoProject | None = None) -> str:
        result = "-"
        if obj:
            result = obj.get_projecteffort_display()
        return result

    @admin.display(description=_("GITHUBプロジェクト"))
    def show_github_project_html_url(self, obj: KippoProject) -> str:
        url = ""
        if obj.github_project_html_url:
            url = format_html('<a href="{url}">{url}</a>', url=obj.github_project_html_url)
        return url

    def save_formset(self, request: DjangoRequest, form: Form, formset: BaseFormSet, change: bool) -> None:
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if instance._state.adding:  # Only for create (needed for handling uuid field as id)
                instance.created_by = request.user  # only update created_by once!
            instance.updated_by = request.user
            instance.save()
        formset.save_m2m()

    def get_form(self, request: DjangoRequest, obj: KippoProject | None = None, **kwargs) -> Form:
        """Set defaults based on request user"""
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)
        # closed projects: every field is readonly, so base_fields is empty — skip the editable-form tweaks
        if obj is not None and obj.is_closed:
            return form
        form.base_fields["project_manager"].initial = request.user.id
        try:
            user_initial_organization, user_organizations = get_user_session_organization(request)
            user_memberships = request.user.memberships.all()
        except ValueError:
            user_initial_organization = None
            user_memberships = request.user.memberships.none()
        if not user_initial_organization:
            self.message_user(
                request,
                "OrganizationMembership not defined for user! Must belong to an Organization to create a project",
                level=messages.ERROR,
            )
        form.base_fields["organization"].initial = user_initial_organization
        form.base_fields["organization"].queryset = user_memberships
        # On /add/, when the user belongs to exactly one organization there's nothing to choose —
        # preselect it and hide the field. The queryset is still scoped to the user's orgs so a
        # tampered submission gets rejected by the field's normal validation.
        if obj is None and user_initial_organization and len(user_organizations) == 1:
            form.base_fields["organization"].widget = forms.HiddenInput()

        # remove add/change/delete buttons from all ForeignKey fields
        for fieldname in form.base_fields:
            form.base_fields[fieldname].widget.can_add_related = False
            form.base_fields[fieldname].widget.can_change_related = False
            form.base_fields[fieldname].widget.can_delete_related = False

        # parent_project: optional selectable field on add (GET-prefilled by the upsell close-action flow);
        # readonly on change form (handled in get_readonly_fields).
        # Required when category is an upsell value — enforced by KippoProjectAdminForm.clean().
        # Scope queryset to the new project's organization — cross-org parent/child relationships make
        # no business sense; the form's clean() rejects mismatches at submit-time.
        if obj is None and "parent_project" in form.base_fields and user_initial_organization:
            form.base_fields["parent_project"].queryset = KippoProject.objects.filter(organization=user_initial_organization)

        # customer: scope queryset to the project's organization (on change) or the user's orgs (on add).
        self._scope_customer_queryset(form, obj, user_memberships)

        # Arriving from the customer admin's "プロジェクトを追加" button (?customer=<pk>): the customer is
        # already chosen, so hide the field. The value is still prefilled (get_changeform_initial_data)
        # and POSTed; the scoped queryset validates it server-side.
        if obj is None and request.GET.get("customer") and "customer" in form.base_fields:
            form.base_fields["customer"].widget = forms.HiddenInput()

        if obj is None and request.GET.get("_upsell_source") == "close":
            self._apply_upsell_source_widgets(form, user_memberships)
        return form

    @staticmethod
    def _scope_customer_queryset(form: Form, obj: KippoProject | None, user_memberships: models.QuerySet) -> None:
        """Scope the customer queryset to the project's organization (or user's orgs on /add/)."""
        if "customer" not in form.base_fields:
            return
        if obj is not None and obj.organization_id:
            form.base_fields["customer"].queryset = KippoCustomer.objects.filter(organization=obj.organization)
        else:
            form.base_fields["customer"].queryset = KippoCustomer.objects.filter(organization__in=user_memberships)

    @staticmethod
    def _apply_upsell_source_widgets(form: Form, user_memberships: models.QuerySet) -> None:
        """Hide parent_project + organization on the upsell close-action redirect.

        Values still POST and the existing validator runs; only the widgets are swapped to hidden.
        parent_project queryset is widened to all of the user's orgs because the default scope
        (user_initial_organization) may not match the parent's org for multi-org users.
        """
        if "parent_project" in form.base_fields:
            form.base_fields["parent_project"].widget = forms.HiddenInput()
            form.base_fields["parent_project"].queryset = KippoProject.objects.filter(organization__in=user_memberships)
        if "organization" in form.base_fields:
            form.base_fields["organization"].widget = forms.HiddenInput()

    def get_readonly_fields(self, request: DjangoRequest, obj: KippoProject | None = None) -> tuple[str, ...]:
        readonly_fields = tuple(super().get_readonly_fields(request, obj))
        # show parent_project as readonly on the change form so admins can see the upsell parent
        if obj is not None and "parent_project" not in readonly_fields:
            readonly_fields = (*readonly_fields, "parent_project")
        # MTG calendar link fields are computed displays — readonly on the change form (kippo#13)
        if obj is not None:
            for fieldname in ("meeting_calendar_url_field", "meeting_description_tag_field"):
                if fieldname not in readonly_fields:
                    readonly_fields = (*readonly_fields, fieldname)
        # forecast on non-closed projects only (per kippo#224 C2 + #226)
        if obj is not None and not obj.is_closed and "estimated_completion_date" not in readonly_fields:
            readonly_fields = (*readonly_fields, "estimated_completion_date")
        # closed projects: lock every editable field — use the re-open action to edit
        if obj is not None and obj.is_closed:
            excluded = set(self.exclude or ())
            locked = tuple(
                f.name
                for f in self.model._meta.get_fields()
                if getattr(f, "editable", False) and not f.auto_created and f.name not in excluded and f.name not in readonly_fields
            )
            readonly_fields = (*readonly_fields, *locked)
        return readonly_fields

    @admin.display(description=_("Estimated Completion Date"))
    def estimated_completion_date(self, obj: KippoProject | None = None) -> str:
        from .exceptions import ProjectStartDateRequiredError
        from .services.forecast import ProjectAssignmentForecastManager

        if obj is None or obj.pk is None:
            return ""
        try:
            result = ProjectAssignmentForecastManager(obj).compute()
        except ProjectStartDateRequiredError:
            return _("(start_date required)")
        return _format_estimated_completion(result)

    @staticmethod
    def _render_copy_field(copy_text: str, display_html: str) -> str:
        """Render a readonly value, a copy-to-clipboard button, and a polite aria-live status slot.

        The button and the status slot are driven by the handler in change_form.html.
        """
        return format_html(
            '<span class="kippo-copy-field">{}'
            '<button type="button" class="button kippo-copy-button" data-clipboard-text="{}">{}</button>'
            '<span class="kippo-copy-status" role="status" aria-live="polite"></span></span>',
            display_html,
            copy_text,
            _("コピー"),
        )

    @admin.display(description=_("MTG カレンダー作成 URL"))
    def meeting_calendar_url_field(self, obj: KippoProject | None = None) -> str:
        if obj is None or obj._state.adding:
            return ""
        url = obj.get_meeting_calendar_template_url()
        link_html = format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, _("Create Project Meeting"))
        return self._render_copy_field(url, link_html)

    @admin.display(description=_("カレンダーの説明欄"))
    def meeting_description_tag_field(self, obj: KippoProject | None = None) -> str:
        if obj is None or obj._state.adding:
            return ""
        tag = obj.get_dsearch_tag()
        return self._render_copy_field(tag, format_html("<code>{}</code>", tag))

    def get_changeform_initial_data(self, request: DjangoRequest) -> dict:
        initial = super().get_changeform_initial_data(request)
        category = request.GET.get("category")
        if category:
            initial["category"] = category
        parent_project_id = request.GET.get("parent_project")
        if parent_project_id:
            initial["parent_project"] = parent_project_id
        organization_id = request.GET.get("organization")
        if organization_id:
            initial["organization"] = organization_id
        customer_id = request.GET.get("customer")
        if customer_id:
            initial["customer"] = customer_id
        return initial

    def _safe_return_to(self, request: DjangoRequest) -> str | None:
        """The validated `_return_to` admin URL (where the user came from), or None.

        The add/change form posts to its own URL with the query string intact (empty form
        action), so `_return_to` survives into response_add/response_change via request.GET.
        """
        return_to = request.GET.get(RETURN_TO_PARAM)
        if return_to and url_has_allowed_host_and_scheme(return_to, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return return_to
        return None

    def _redirect_back_after_save(self, request: DjangoRequest, response: HttpResponse) -> HttpResponse:
        return_to = self._safe_return_to(request)
        if not return_to:
            return response
        # "Save and add another" / "Save and continue editing" keep the user in the project
        # add/change flow — carry _return_to (and customer/organization prefill) forward so the
        # eventual plain Save still returns to the originating page. Plain Save goes back now.
        if any(key in request.POST for key in ("_addanother", "_continue", "_saveasnew")):
            if isinstance(response, HttpResponseRedirect):
                carry = {RETURN_TO_PARAM: return_to}
                for key in ("customer", "organization"):
                    value = request.GET.get(key)
                    if value:
                        carry[key] = value
                response["Location"] = self._with_params(response["Location"], carry)
            return response
        return HttpResponseRedirect(return_to)

    @staticmethod
    def _with_params(url: str, params: dict[str, str]) -> str:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        query.update({key: [value] for key, value in params.items()})
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))

    def response_add(self, request: DjangoRequest, obj: KippoProject, post_url_continue: str | None = None):
        return self._redirect_back_after_save(request, super().response_add(request, obj, post_url_continue))

    def response_change(self, request: DjangoRequest, obj: KippoProject):
        return self._redirect_back_after_save(request, super().response_change(request, obj))

    def save_model(self, request: DjangoRequest, obj: KippoProject, form: Form, change: bool):
        if obj.pk is None:
            # expect only not not exist IF creating a new Project via ADMIN
            obj.created_by = request.user
            obj.updated_by = request.user
        else:
            obj.updated_by = request.user

        # columnset is hidden from the form (see get_exclude) — auto-assign the organization's
        # default on create; existing projects keep their stored columnset.
        if obj.columnset_id is None and obj.organization_id:
            default_columnset = obj.organization.get_default_columnset()
            if default_columnset is not None:
                obj.columnset = default_columnset

        super().save_model(request, obj, form, change)

    def get_ordering(self, request: DjangoRequest):
        # non-project category first, then confidence desc, target_date asc, name. A self-contained
        # Case expression (not an annotation name) so order_by() resolves it on any queryset —
        # ModelAdmin.get_queryset() applies get_ordering() on the raw manager qs (before any
        # annotate()), and the ChangeList applies it again after get_queryset(). Defining it here
        # (not via the `ordering` attribute) is what makes the changelist actually honor it.
        # KippoProject.name is unique, so this is already a deterministic total ordering.
        return [
            Case(When(category__key="non-project", then=Value(0)), default=Value(1)),
            "-confidence",
            "target_date",
            "name",
        ]

    def get_queryset(self, request: DjangoRequest):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations).distinct()


@admin.register(KippoProject)
class KippoProjectAdmin(KippoProjectBaseAdmin):
    # All projects, including closed/inactive — keep display_as_active so the active/closed state
    # is visible at a glance (the only column that differs from the active admin).
    list_display = (*KippoProjectBaseAdmin.list_display, "display_as_active")


@admin.register(ActiveKippoProject)
class ActiveKippoProjectAdmin(KippoProjectBaseAdmin):
    # Identical to the base except the queryset: the ActiveKippoProjectManager (proxy default
    # manager) restricts it to open + display_as_active projects. The only form difference is
    # below — closure fields never apply to an active project.

    def get_exclude(self, request: DjangoRequest, obj: KippoProject | None = None):
        excluded: list[str] = list(super().get_exclude(request, obj) or ())
        # Active projects are never closed (filtered by ActiveKippoProjectManager); hide closure fields
        for field in PROJECT_CLOSURE_FIELDS:
            if field not in excluded:
                excluded.append(field)
        # parent_project is only relevant on add (manual upsell creation); hide on change
        if obj is not None and "parent_project" not in excluded:
            excluded.append("parent_project")
        return tuple(excluded)

    def get_fieldsets(self, request: DjangoRequest, obj: KippoProject | None = None):
        # Billing/revenue is managed on KippoProjectContractAdmin, so drop the read-only Billing
        # summary fieldset here (it stays on the full KippoProjectAdmin).
        return [fieldset for fieldset in super().get_fieldsets(request, obj) if fieldset[0] != BILLING_FIELDSET_LABEL]


@admin.register(KippoProjectContract)
class KippoProjectContractAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    # Standalone admin so the contract's billing ledger (which belongs to the contract, not the
    # project — kippo#31) can be edited here. The contract itself is also editable as an inline on
    # the project (KippoProjectContractInline); this page adds the billing-entries inline.
    list_display = ("project", "billing_type", "total_amount", "start_date", "end_date")
    list_filter = ("billing_type",)
    search_fields = ("project__name",)
    raw_id_fields = ("project",)
    inlines = [KippoProjectBillingEntryInline]
    actions = ["generate_billing_entries"]

    @admin.action(description=_("契約から請求エントリを生成"))
    def generate_billing_entries(self, request: DjangoRequest, queryset: models.QuerySet):
        created_count = 0
        for contract in queryset:
            created_count += len(contract.generate_billing_entries(created_by=request.user))
        self.message_user(request, _("%d billing entries created.") % created_count, level=messages.INFO)


@admin.register(KippoMilestone)
class KippoMilestoneAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        "title",
        "get_project_name",
        "get_task_count",
        "is_completed",
        "start_date",
        "target_date",
        "actual_date",
        "updated_by",
        "updated_datetime",
    )
    readonly_fields = ("project",)
    search_fields = ("title", "description")
    ordering = ("project", "target_date")

    def get_project_name(self, obj: KippoMilestone):
        return obj.project.name

    get_project_name.short_description = _("Project")

    def get_task_count(self, obj: KippoMilestone) -> int:
        result = 0
        if obj:
            result = obj.kippotask_milestone.count()
        return result

    get_task_count.short_description = _("Task Count")

    def response_add(self, request: DjangoRequest, obj: KippoMilestone, post_url_continue: bool | None = None):
        """Overridding Redirect to the KippoProject page after edit."""
        project_url = obj.project.get_admin_url()
        return HttpResponseRedirect(project_url)

    def response_change(self, request: DjangoRequest, obj: KippoMilestone):
        """Overriding Redirect to the KippoProject page after edit."""
        project_url = obj.project.get_admin_url()
        return HttpResponseRedirect(project_url)

    def get_queryset(self, request: DjangoRequest):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(project__organization__in=request.user.organizations).order_by("project__organization").distinct()


class ProjectColumnInline(admin.TabularInline):
    model = ProjectColumn
    extra = 3


@admin.register(ProjectColumnSet)
class ProjectColumnSetAdmin(UserCreatedBaseModelAdmin):
    list_display = ("name", "get_column_names")
    inlines = [ProjectColumnInline]


def auto_extend_projectmonthlyassignment_action(
    modeladmin: admin.ModelAdmin,
    request: DjangoRequest,
    queryset: models.QuerySet,
) -> None:
    """Run `auto_create_future_assignments` once per distinct project in the selection (kippo#19)."""
    from .services.autoassign import auto_create_future_assignments

    distinct_projects = {row.project_id: row.project for row in queryset.select_related("project")}
    if not distinct_projects:
        modeladmin.message_user(request, _("No assignments selected."), level=messages.ERROR)
        return

    total_created = 0
    for project in distinct_projects.values():
        created_rows, skip_reason = auto_create_future_assignments(project, request.user)
        total_created += len(created_rows)
        if skip_reason is not None:
            modeladmin.message_user(
                request,
                _("Project '%(name)s' skipped: %(reason)s") % {"name": project.name, "reason": skip_reason.value},
                level=messages.WARNING,
            )
        else:
            modeladmin.message_user(
                request,
                _("Project '%(name)s': created %(count)d future-month rows.") % {"name": project.name, "count": len(created_rows)},
                level=messages.INFO,
            )
    if total_created:
        modeladmin.message_user(
            request,
            _("Total future-month rows created: %(count)d") % {"count": total_created},
            level=messages.SUCCESS,
        )


auto_extend_projectmonthlyassignment_action.short_description = _("将来月の割当を生成")  # noqa: E305


@admin.register(ProjectMonthlyAssignment)
class ProjectMonthlyAssignmentAdmin(UserCreatedBaseModelAdmin):
    list_display = ("project", "get_project_organization", "user", "month", "percentage", "is_confirmed")
    list_filter = ("is_confirmed", "month", "project__organization")
    search_fields = ("project__name", "user__username")
    actions = (auto_extend_projectmonthlyassignment_action,)

    def get_project_organization(self, obj: ProjectMonthlyAssignment):
        organization_name = obj.project.organization.name
        return organization_name

    get_project_organization.short_description = _("Organization")


@admin.register(ProjectMonthlyCost)
class ProjectMonthlyCostAdmin(admin.ModelAdmin):
    list_display = ("project", "get_project_organization", "month", "service", "cost", "currency")
    list_filter = ("service", "currency")
    search_fields = ("project__id", "project__name")
    ordering = ("project", "-month")
    formfield_overrides = {JSONField: {"widget": PrettyJSONWidget}}

    def get_readonly_fields(self, request: DjangoRequest, obj: ProjectMonthlyCost | None = None) -> tuple:
        if obj:  # Editing existing object
            return ("project", "month", "service", "cost", "currency", "itemized_cost_display")
        return ()

    def get_project_organization(self, obj: ProjectMonthlyCost) -> str:
        return obj.project.organization.name

    get_project_organization.short_description = _("Organization")

    @admin.display(description=_("Itemized Cost"))
    def itemized_cost_display(self, obj: ProjectMonthlyCost) -> str:
        """Display itemized_cost as pretty-printed JSON."""
        if not obj.itemized_cost:
            return "-"
        formatted_json = json.dumps(obj.itemized_cost, indent=2, ensure_ascii=False, sort_keys=True)
        return format_html("<pre style='margin: 0; white-space: pre-wrap;'>{}</pre>", formatted_json)

    def has_module_permission(self, request: DjangoRequest) -> bool:
        return request.user.is_superuser

    def has_view_permission(self, request: DjangoRequest, obj: ProjectMonthlyCost | None = None) -> bool:
        return request.user.is_superuser

    def has_add_permission(self, request: DjangoRequest) -> bool:
        return request.user.is_superuser

    def has_change_permission(self, request: DjangoRequest, obj: ProjectMonthlyCost | None = None) -> bool:
        return request.user.is_superuser

    def has_delete_permission(self, request: DjangoRequest, obj: ProjectMonthlyCost | None = None) -> bool:
        return request.user.is_superuser

    def get_queryset(self, request: DjangoRequest) -> models.QuerySet:
        qs = super().get_queryset(request)
        return qs.filter(project__organization__in=request.user.organizations).order_by("project__organization")


@admin.register(CollectIssuesAction)
class CollectIssuesActionAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        "id",
        "organization",
        "start_datetime",
        "end_datetime",
        "status",
        "new_task_count",
        "new_taskstatus_count",
        "updated_taskstatus_count",
    )


class ProjectWeeklyEffortAdminForm(forms.ModelForm):
    """Blocks non-superusers from creating/moving effort into a closed week (kippo#33 / T17).

    `request_user` is set per-request by ProjectWeeklyEffortAdmin.get_form.
    """

    request_user = None

    class Meta:
        model = ProjectWeeklyEffort
        fields = "__all__"  # noqa: DJ007

    def clean(self):
        cleaned = super().clean()
        if self.request_user is None or self.request_user.is_superuser:
            return cleaned
        project = cleaned.get("project")
        week_start = cleaned.get("week_start")
        effort_user = cleaned.get("user")
        if project and week_start and effort_user and project.organization.is_weeklyeffort_closed(effort_user, week_start):
            raise ValidationError(WEEKLY_EFFORT_CLOSED_MESSAGE, code="weekly_effort_closed")
        return cleaned


@admin.register(ProjectWeeklyEffort)
class ProjectWeeklyEffortAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    form = ProjectWeeklyEffortAdminForm
    list_display = ("get_project_name", "week_start", "get_user_display_name", "hours")
    ordering = ("project", "-week_start", "user")
    search_fields = (
        "project__name",
        "user__last_name",
    )
    actions = ("download_csv", "download_monthly_csv")

    def has_change_permission(self, request: DjangoRequest, obj: ProjectWeeklyEffort | None = None) -> bool:
        if obj and not request.user.is_superuser and obj.is_closed():
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request: DjangoRequest, obj: ProjectWeeklyEffort | None = None) -> bool:
        if obj and not request.user.is_superuser and obj.is_closed():
            return False
        return super().has_delete_permission(request, obj)

    def get_list_filter(self, request: DjangoRequest) -> list:
        current_month_start, current_month_end = get_current_month_date_range()
        return [
            ("week_start", DateRangeFilterBuilder(title="date filter", default_start=current_month_start, default_end=current_month_end)),
        ]

    def get_project_name(self, obj: ProjectWeeklyEffort | None = None) -> str:
        result = "-"
        if obj and obj.project and obj.project.name:
            result = obj.project.name
        return result

    get_project_name.short_description = _("Project")

    def get_user_display_name(self, obj: ProjectWeeklyEffort | None = None) -> str:
        result = "-"
        if obj:
            result = obj.user.display_name
        return result

    get_user_display_name.short_description = _("user")

    @admin.action(description=_("Download ProjectWeeklyEffort CSV"))
    def download_csv(self, request: DjangoRequest, queryset: models.QuerySet) -> HttpResponseRedirect | None:
        if not ProjectWeeklyEffort.objects.filter(project__organization__in=request.user.organizations).exists():
            self.message_user(request, _("No ProjectWeeklyEffort exists!"), level=messages.WARNING)
            return None
        # initiate creation
        now = timezone.now()
        filename = now.strftime("project-effort-%Y%m%d%H%M%S.csv")
        key = f"tmp/download/{filename}"
        selected_query_id = list(queryset.values_list("id", flat=True))
        generate_projectweeklyeffort_csv(user_id=str(request.user.pk), key=key, effort_ids=selected_query_id)
        # redirect to waiter
        urlencoded_key = urllib.parse.quote_plus(key)
        download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}"
        return HttpResponseRedirect(redirect_to=download_waiter_url)

    @admin.action(description="Download ProjectMonthlyEffort CSV")
    def download_monthly_csv(self, request: DjangoRequest, queryset: models.QuerySet):
        if not ProjectWeeklyEffort.objects.filter(project__organization__in=request.user.organizations).exists():
            self.message_user(request, _("No ProjectWeeklyEffort exists!"), level=messages.WARNING)
            return None
        # initiate creation
        now = timezone.localtime()
        filename = now.strftime("project-monthly-effort-%Y%m%d%H%M%S.csv")
        key = f"tmp/download/{filename}"
        selected_query_id = list(queryset.values_list("id", flat=True))
        generate_projectmonthlyeffort_csv(user_id=str(request.user.pk), key=key, effort_ids=selected_query_id)
        # redirect to waiter
        urlencoded_key = urllib.parse.quote_plus(key)
        download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}"
        return HttpResponseRedirect(redirect_to=download_waiter_url)

    def save_model(self, request: DjangoRequest, obj: ProjectWeeklyEffort, form: Form, change: bool):
        """Force the entry's user to the requesting user unless they are a superuser.

        Guards against a tampered submission setting a different `user`; the get_form widget below
        only hides the field in the UI for non-superusers.
        """
        if not request.user.is_superuser:
            obj.user = request.user
        super().save_model(request, obj, form, change)

    def get_form(self, request: DjangoRequest, obj: ProjectWeeklyEffort | None = None, **kwargs):
        """Set defaults based on request user"""
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)
        form.request_user = request.user  # consumed by ProjectWeeklyEffortAdminForm.clean (kippo#33 / T17)
        form.base_fields["user"].initial = request.user.id
        try:
            user_initial_organization, user_organizations = get_user_session_organization(request)
            user_memberships = request.user.memberships.all()
        except ValueError:
            user_initial_organization = None
            user_memberships = request.user.memberships.none()
        if not user_initial_organization:
            self.message_user(
                request,
                "OrganizationMembership not defined for user! Must belong to an Organization to create a project",
                level=messages.ERROR,
            )
        if request.user.is_superuser:
            # Superusers may log effort for any user belonging to an organization they are a member of.
            member_user_ids = OrganizationMembership.objects.filter(organization__in=user_memberships).values_list("user__id", flat=True)
            form.base_fields["user"].queryset = KippoUser.objects.filter(id__in=member_user_ids).order_by("last_name", "username")
        else:
            # Non-superusers may only log their own effort, so the user field is hidden and forced
            # to the requesting user (see save_model).
            form.base_fields["user"].widget = forms.HiddenInput()
        user_projects = KippoProject.objects.filter(organization__in=user_memberships)
        form.base_fields["project"].initial = user_projects.first()
        form.base_fields["project"].queryset = user_projects
        return form

    def get_queryset(self, request: DjangoRequest) -> models.QuerySet:
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(project__organization__in=request.user.organizations).order_by("project__organization")

    def get_fiscal_year_org_per_user_weeklyeffort(self, organizations: Iterable[KippoOrganization]) -> tuple:  # noqa: C901, PLR0912
        from accounts.models import PublicHoliday

        all_months = set()
        monthly_expected_hours = Counter()

        monthly_week_starts = []
        results = {}
        now = timezone.now()
        monthly_expected_hours_processed = False
        for org in organizations:
            user_weekstarts = defaultdict(list)
            results[org.name] = {}
            if now.month < org.fiscalyear_start_month:
                current_fiscal_year = timezone.datetime(now.year - 1, org.fiscalyear_start_month, 1, tzinfo=timezone.timezone.utc)
            else:
                current_fiscal_year = timezone.datetime(now.year, org.fiscalyear_start_month, 1, tzinfo=timezone.timezone.utc)
            # get organization users
            # -- aggregate projectweeklyeffort per user per month
            users = org.get_membership_kippousers()
            projectweeklyeffort = ProjectWeeklyEffort.objects.filter(
                user__in=users, week_start__gte=current_fiscal_year.date(), project__organization=org
            )
            sum_index = 0
            flag_index = 1
            for effort in projectweeklyeffort:
                if effort.user.username not in results[org.name]:
                    results[org.name][effort.user.username] = {}
                if effort.week_start.month not in results[org.name][effort.user.username]:
                    results[org.name][effort.user.username][effort.week_start.month] = [0, False]
                results[org.name][effort.user.username][effort.week_start.month][sum_index] += effort.hours
                user_weekstarts[effort.user.username].append(effort.week_start)

            # remove public holidays from total
            # -- calculate total workdays from fiscal start
            if not monthly_expected_hours:
                current = current_fiscal_year
                while current <= now:
                    all_months.add(current.month)
                    if current.weekday() < SATURDAY:  # SAT=5, SUN=6
                        monthly_expected_hours[current.month] += 1
                    if current.weekday() == 0:
                        monthly_week_starts.append(current.date())
                    current += timezone.timedelta(days=1)
            # apply hours
            for month in monthly_expected_hours:
                if not monthly_expected_hours_processed:
                    monthly_expected_hours[month] *= org.day_workhours
                # -- update user dictionaries with 0s
                for org_user_info in results.values():
                    for user_month_data in org_user_info.values():
                        if month and month not in user_month_data:
                            user_month_data[month] = [0, False]
                        elif (
                            user_month_data[month][sum_index]
                            > monthly_expected_hours[month] + monthly_expected_hours[month] * settings.PROJECT_EFFORT_EXCEED_PERCENTAGE
                        ):
                            user_month_data[month][flag_index] = True
            monthly_expected_hours_processed = True
            # re-sort user_data
            for org_key in results:  # noqa: PLC0206
                for user_key in results[org_key].keys():  # noqa: PLC0206
                    if "missing" not in results[org_key][user_key]:
                        this_week_start = now
                        while this_week_start.weekday() != 0:
                            this_week_start -= timezone.timedelta(days=1)

                        results[org_key][user_key] = dict(sorted(results[org_key][user_key].items()))
                        # add missing
                        user_missing_weekstarts = set(monthly_week_starts) - set(user_weekstarts[user_key])
                        results[org_key][user_key]["missing"] = [
                            ", ".join(d.strftime("%m-%d") for d in sorted(user_missing_weekstarts) if d != this_week_start.date()),
                            False,
                        ]  # noqa: PLC0206

        # -- calculate public holidays
        for holiday in PublicHoliday.objects.filter(day__gte=current_fiscal_year.date(), day__lte=now):
            # -- subtract public holidays from current total
            monthly_expected_hours[holiday.day.month] -= 1 * org.day_workhours

        return dict(results), dict(monthly_expected_hours), tuple(all_months)

    def changelist_view(self, request: DjangoRequest, extra_context: dict | None = None):
        original_response = super().changelist_view(request, extra_context)
        organizations = request.user.organizations
        summary_results, expected_hours, all_months = self.get_fiscal_year_org_per_user_weeklyeffort(organizations)

        extra_context = dict(
            self.admin_site.each_context(request),
            summary=summary_results,
            expected=expected_hours,
            months=all_months,
            monthly_exceed_percentage=int(settings.PROJECT_EFFORT_EXCEED_PERCENTAGE * 100),
        )
        if hasattr(original_response, "context_data") and original_response.context_data:
            extra_context.update(original_response.context_data)
        elif isinstance(original_response, HttpResponseRedirect):
            return original_response
        return TemplateResponse(request, "admin/projects/weeklyeffortadmin.html", extra_context)


@admin.register(ProjectWeeklyEffortUnlock)
class ProjectWeeklyEffortUnlockAdmin(UserCreatedBaseModelAdmin):
    """週間稼働アンロックの管理・承認 (kippo#33 / T18).

    AllowIsStaffAdminMixin is deliberately NOT applied: staff could otherwise unlock their own
    closed weeks. Default Django model permissions apply (superusers, or staff explicitly granted).
    REST API 経由の申請はここで承認する。adminが直接作成したアンロックは作成時に自動承認される
    (ただし superuser を除き自分自身のためのものは保留のまま — 自分の週を勝手に開けられない原則)。
    """

    list_display = (
        "organization",
        "user",
        "week_start",
        "get_reason_short",
        "is_active",
        "approved_by",
        "approved_datetime",
        "expires_datetime",
        "created_by",
    )
    list_filter = ("organization", "approved_datetime")
    ordering = ("-week_start", "user")
    search_fields = ("user__username", "user__last_name", "reason")
    actions = ("approve_selected",)

    REASON_PREVIEW_CHARS = 40

    @admin.display(description=_("理由"))
    def get_reason_short(self, obj: ProjectWeeklyEffortUnlock) -> str:
        if not obj.reason:
            return "-"
        limit = self.REASON_PREVIEW_CHARS
        return f"{obj.reason[:limit]}…" if len(obj.reason) > limit else obj.reason

    @admin.display(boolean=True, description=_("有効"))
    def is_active(self, obj: ProjectWeeklyEffortUnlock) -> bool:
        return obj.is_active()

    def save_model(self, request: HttpRequest, obj: ProjectWeeklyEffortUnlock, form: Form, change: bool) -> None:
        creating = getattr(obj, "pk", None) is None
        super().save_model(request, obj, form, change)  # sets created_by/updated_by and saves
        # adminが申請フローを経ず直接作成したアンロックは即承認する (他ユーザ向けのみ; superuserは自分向けも可)
        if creating and not obj.is_approved and (request.user.is_superuser or obj.user_id != request.user.pk):
            obj.approve(approved_by=request.user, expires_datetime=obj.expires_datetime)

    @admin.action(description=_("選択したアンロック申請を承認する"))
    def approve_selected(self, request: HttpRequest, queryset: "QuerySet[ProjectWeeklyEffortUnlock]") -> None:
        approved = 0
        skipped_self = 0
        for unlock in queryset:
            if unlock.is_approved:
                continue
            if unlock.created_by_id == request.user.pk and not request.user.is_superuser:
                skipped_self += 1
                continue
            unlock.approve(approved_by=request.user, expires_datetime=unlock.expires_datetime)
            approved += 1
        if approved:
            self.message_user(request, f"{approved}件のアンロックを承認しました。", messages.SUCCESS)
        if skipped_self:
            self.message_user(request, f"{skipped_self}件は自分の申請のため承認をスキップしました。", messages.WARNING)


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

        def get_project_display_name(project: KippoProject) -> str:
            return project.name

        if "project" in form.base_fields:
            user_organizations = request.user.organizations
            open_projects = (
                KippoProject.objects.filter(is_closed=False, organization__in=user_organizations)
                .exclude(category__key="non-project")
                .order_by("name")
            )
            form.base_fields["project"].initial = open_projects.first()
            form.base_fields["project"].queryset = open_projects
            form.base_fields["project"].label_from_instance = get_project_display_name
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

        def get_project_display_name(project: KippoProject) -> str:
            return project.name

        if "project" in form.base_fields:
            user_organizations = request.user.organizations
            open_projects = KippoProject.objects.filter(is_closed=False, organization__in=user_organizations, category__key="non-project").order_by(
                "name"
            )
            form.base_fields["project"].initial = open_projects.first()
            form.base_fields["project"].queryset = open_projects
            form.base_fields["project"].label_from_instance = get_project_display_name
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
