import datetime
from collections.abc import Sequence
from typing import TYPE_CHECKING

from accounts.models import KippoOrganization, KippoUser
from commons.fields import CommaSeparatedField
from commons.viewsets import organization_ids_for_user, scope_to_user_organizations, user_can_access_organization
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from .definitions import (
    BILLING_TYPE_MONTHLY,
    PRICING_BASIS_EFFORT,
    PRICING_BASIS_FIXED,
    SURVEY_EFFORT_THRESHOLD_PERCENTAGE,
    WEEKLY_EFFORT_CLOSED_MESSAGE,
    ProjectProgressStatus,
    ProjectRoles,
)
from .functions import previous_week_startdate
from .models import (
    PHASE_UNDER_CONTRACT,
    UNDER_CONTRACT_REQUIRES_CONTRACT_MSG,
    KippoProject,
    KippoProjectBillingEntry,
    KippoProjectContract,
    KippoProjectOrganizationCategory,
    KippoProjectPhaseConfidenceChange,
    KippoProjectStatus,
    KippoProjectUserStatisfactionResult,
    ProjectAssignmentRate,
    ProjectColumnSet,
    ProjectMonthlyAssignment,
    ProjectMonthlyCost,
    ProjectParticipant,
    ProjectWeeklyEffort,
    ProjectWeeklyEffortUnlock,
)
from .services.progress import EFFORT_LEVELS, effort_level, public_holidays_by_country, user_effort_shares

if TYPE_CHECKING:
    from accounts.models import OrganizationMembership


class ProjectAssignmentRateInlineSerializer(serializers.Serializer):
    """Inline serializer for assignment rate response in OpenAPI schema."""

    role = serializers.CharField()
    rate_per_day = serializers.IntegerField()
    is_default = serializers.BooleanField()


class MonthlyBillingScheduleEntrySerializer(serializers.Serializer):
    """One planned monthly billing row (kippo#39 / T15): month-end date + amount."""

    month = serializers.DateField(help_text="Month-end (月末) billing date.")
    amount = serializers.DecimalField(max_digits=12, decimal_places=0, help_text="Billed amount for the month (JPY).")


class ProjectProgressStatusInlineSerializer(serializers.Serializer):
    """Inline serializer for project progress status in OpenAPI schema."""

    current_effort_hours = serializers.IntegerField()
    expected_effort_hours = serializers.IntegerField(allow_null=True)
    allocated_effort_hours = serializers.IntegerField(allow_null=True)
    is_estimated_allocated_effort_hours = serializers.BooleanField(
        help_text="True when allocated_effort_hours is estimated from a 納品/固定 contract (契約金額 ÷ 人日単価) "
        "because the project has no 割当工数(人日) entered.",
    )
    difference_percentage = serializers.FloatField(allow_null=True)
    level = serializers.ChoiceField(
        choices=EFFORT_LEVELS,
        help_text="稼働状況 level from difference_percentage: neutral (no comparison), success (at or under expected), "
        "warning (over expected), danger (over by more than the exceeding threshold).",
    )


class WeeklyEffortUserInlineSerializer(serializers.Serializer):
    """Inline serializer for weekly effort user data in OpenAPI schema."""

    user_id = serializers.IntegerField()
    username = serializers.CharField()
    display_name = serializers.CharField()
    hours = serializers.IntegerField()
    percentage = serializers.FloatField()


class LatestCommentInlineSerializer(serializers.Serializer):
    """Inline serializer for latest project status comment in OpenAPI schema."""

    comment = serializers.CharField()
    created_by_username = serializers.CharField(allow_null=True)
    created_by_display_name = serializers.CharField(allow_null=True)
    created_datetime = serializers.DateTimeField()


class SurveyUserInlineSerializer(serializers.Serializer):
    """Inline serializer for survey completion user data in OpenAPI schema."""

    user_id = serializers.IntegerField()
    username = serializers.CharField()
    display_name = serializers.CharField()
    percentage = serializers.FloatField()
    survey_completed = serializers.BooleanField()


class GithubRepositoryInlineSerializer(serializers.Serializer):
    """Inline serializer for GithubRepository links in OpenAPI schema."""

    repository_url = serializers.URLField()


class OrganizationMemberSerializer(serializers.Serializer):
    """Minimal projection of a `KippoUser` + their `OrganizationMembership` for use as a
    user-picker source on the kippo-ui add-assignment modal (kippo-ui#57). Per kippo#233.
    """

    user_id = serializers.UUIDField(help_text="KippoUser primary key.")
    username = serializers.CharField()
    display_name = serializers.CharField(help_text="Composed first + last + (github_login).")
    github_login = serializers.CharField(allow_blank=True)
    is_developer = serializers.BooleanField()
    is_project_manager = serializers.BooleanField()


class ProjectAssignmentRateSerializer(serializers.ModelSerializer):
    """Serializer for ProjectAssignmentRate model."""

    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = ProjectAssignmentRate
        fields = ["id", "project", "project_name", "role", "rate_per_day", "created_datetime", "updated_datetime"]
        read_only_fields = ["id", "project_name", "created_datetime", "updated_datetime"]


class KippoProjectOrganizationCategorySerializer(serializers.ModelSerializer):
    """Read/write serializer for KippoProjectOrganizationCategory.

    Backs both the project category picker (read, kippo#43) and org category management
    (write, kippo#48). Write access is gated by ``IsSuperuserOrOrgMemberForCategory``; this
    serializer additionally (a) runs the model's cross-scope/uniqueness validation so duplicates
    surface as 400s (never a DB IntegrityError 500) and (b) rejects an ``organization`` the
    requester is not a member of (defence-in-depth alongside the permission class).
    """

    class Meta:
        model = KippoProjectOrganizationCategory
        fields = ["id", "key", "label", "organization", "sort_order", "is_active", "is_default"]
        read_only_fields = ["id"]

    def validate_organization(self, value: KippoOrganization | None) -> KippoOrganization | None:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or user.is_superuser:
            return value
        # Non-superusers may only create/keep categories under an org they belong to. A null org
        # (global default) is superuser-only and already blocked by the permission class.
        member_org_ids = organization_ids_for_user(user)
        if value is None or value.pk not in member_org_ids:
            raise serializers.ValidationError(_("You may only manage categories for an organization you are a member of."))
        return value

    def validate(self, attrs: dict) -> dict:
        # Merge incoming attrs over the existing instance (partial updates) and run the model's
        # own validation: field checks, clean() (cross-scope label rule) and the unique constraints.
        merged = {}
        if self.instance is not None:
            merged = {field: getattr(self.instance, field) for field in ("organization", "key", "label", "sort_order", "is_active", "is_default")}
        merged.update(attrs)
        candidate = KippoProjectOrganizationCategory(**merged)
        if self.instance is not None:
            # Reflect the existing row so validate_unique/validate_constraints exclude self
            # (otherwise the update trips the (organization, key)/(organization, label)/pk constraints).
            candidate.pk = self.instance.pk
            candidate._state.adding = False
            candidate._state.db = self.instance._state.db
        try:
            candidate.full_clean(exclude=("created_by", "updated_by", "closed_datetime"))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages) from exc
        return attrs


def _restrict_customer_field_to_user_organizations(serializer: serializers.ModelSerializer, field_name: str) -> None:
    """Restrict a KippoCustomer FK field's choices to the request user's member organizations
    (superusers: all). Bounds write validation AND the browsable-API/OPTIONS enumeration so a user can
    neither view nor assign a customer outside their organizations. The stricter "must belong to the
    project's organization" rule is enforced in each serializer's validate().
    """
    field = serializer.fields.get(field_name)
    request = serializer.context.get("request")
    user = getattr(request, "user", None)
    if field is None or user is None:
        return
    # The field queryset is only evaluated for write validation / browsable-API choices — never on a
    # JSON GET — so this adds no query to list/detail reads.
    field.queryset = scope_to_user_organizations(field.queryset, user)


class KippoProjectContractSerializer(serializers.ModelSerializer):
    """The project's contract (kippo#31) — billing terms. project is set from the nested route."""

    project_name = serializers.CharField(source="project.name", read_only=True)
    # Human-readable 請求先 name for display (parity with project_name / KippoProject.customer_name), so a
    # client can render the billed customer without a second lookup. Null when billed_to is unset.
    billed_to_name = serializers.CharField(source="billed_to.name", read_only=True, allow_null=True)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # 請求先 choices: only the request user's organizations' customers (gap #1). The exact
        # project-organization match is enforced in validate().
        _restrict_customer_field_to_user_organizations(self, "billed_to")

    class Meta:
        model = KippoProjectContract
        fields = [
            "id",
            "project",
            "project_name",
            "billed_to",
            "billed_to_name",
            "billing_type",
            "pricing_basis",
            "total_amount",
            "estimated_monthly_amount",
            "start_date",
            "end_date",
            "note",
            "created_datetime",
            "updated_datetime",
        ]
        # project comes from the URL (nested under projects/) — set in the viewset, not the payload.
        read_only_fields = ["id", "project", "project_name", "billed_to_name", "created_datetime", "updated_datetime"]

    def validate(self, attrs: dict) -> dict:
        # DRF does not run model.clean(); mirror KippoProjectContract.clean() so the API enforces the
        # same invariants as the admin (a fixed-price contract without total_amount otherwise breaks
        # billing generation: total_amount // len(months)). For PATCH, fall back to the stored values.
        instance = self.instance
        billing_type = attrs.get("billing_type", getattr(instance, "billing_type", None))
        pricing_basis = attrs.get("pricing_basis", getattr(instance, "pricing_basis", None))
        total_amount = attrs.get("total_amount", getattr(instance, "total_amount", None))
        estimated_monthly_amount = attrs.get("estimated_monthly_amount", getattr(instance, "estimated_monthly_amount", None))
        start_date = attrs.get("start_date", getattr(instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(instance, "end_date", None))
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError({"end_date": _("Contract start_date is after end_date")})
        if pricing_basis == PRICING_BASIS_FIXED and total_amount is None:
            raise serializers.ValidationError({"total_amount": _("Total amount is required for fixed-price contracts.")})
        # 月額 (kippo#46) only drives effort + monthly billing — reject it elsewhere as a likely mistake.
        if estimated_monthly_amount is not None and not (pricing_basis == PRICING_BASIS_EFFORT and billing_type == BILLING_TYPE_MONTHLY):
            raise serializers.ValidationError(
                {"estimated_monthly_amount": _("Estimated monthly amount (月額) only applies to effort + monthly contracts.")}
            )
        # 請求先 must belong to the contract's project's organization (the project comes from the URL).
        billed_to = attrs.get("billed_to")
        project = self._project_for_validation()
        if billed_to is not None and project is not None and billed_to.organization_id != project.organization_id:
            raise serializers.ValidationError({"billed_to": _("請求先 must belong to the project's organization.")})
        return attrs

    def _project_for_validation(self) -> "KippoProject | None":
        """The contract's project: the instance's on update, else resolved from the nested-route
        ``project_pk`` on create (the payload never carries ``project``).
        """
        if self.instance is not None:
            return self.instance.project
        view = self.context.get("view")
        project_pk = getattr(view, "kwargs", {}).get("project_pk") if view else None
        if project_pk:
            return KippoProject.objects.filter(pk=project_pk).select_related("organization").first()
        return None


def _user_display_name(user: KippoUser | None) -> str | None:
    """The user's first and last name, falling back to the username. None when there is no user."""
    if not user:
        return None
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username


class KippoProjectStatusCommentSerializer(serializers.ModelSerializer):
    """A project's status comment (``KippoProjectStatus``, kippo#64), nested under
    ``/projects/{project_pk}/status-comments/``.

    ``project`` and ``created_by`` are bound from the route and the acting user, never the payload.
    The author fields mirror ``KippoProjectSerializer.latest_comment`` so a client can render either.
    """

    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    created_by_display_name = serializers.SerializerMethodField()

    class Meta:
        model = KippoProjectStatus
        fields = ["id", "project", "comment", "created_by_username", "created_by_display_name", "created_datetime"]
        read_only_fields = ["id", "project", "created_datetime"]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_created_by_display_name(self, obj: KippoProjectStatus) -> str | None:
        return _user_display_name(obj.created_by)


class KippoProjectPhaseConfidenceChangeSerializer(serializers.ModelSerializer):
    """One フェーズ / 確度 change on a project (``KippoProjectPhaseConfidenceChange``, kippo#77), embedded
    in the project response as ``phase_confidence_changes``. Read-only: KippoProject.save() writes the rows.

    ``*_phase`` is the phase key and ``*_phase_display`` its label. The ``changed_by_*`` fields
    follow the status-comment author fields; both are null when no user is recorded (the project
    was saved with ``updated_by`` unset, or the user has since been deleted).
    """

    previous_phase_display = serializers.CharField(source="get_previous_phase_display", read_only=True)
    new_phase_display = serializers.CharField(source="get_new_phase_display", read_only=True)
    changed_by_username = serializers.CharField(source="changed_by.username", read_only=True, allow_null=True)
    changed_by_display_name = serializers.SerializerMethodField()

    class Meta:
        model = KippoProjectPhaseConfidenceChange
        fields = [
            "id",
            "previous_phase",
            "previous_phase_display",
            "new_phase",
            "new_phase_display",
            "previous_confidence",
            "new_confidence",
            "changed_by_username",
            "changed_by_display_name",
            "changed_datetime",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_changed_by_display_name(self, obj: KippoProjectPhaseConfidenceChange) -> str | None:
        return _user_display_name(obj.changed_by)


class KippoProjectBillingEntrySerializer(serializers.ModelSerializer):
    """One entry in a contract's billing ledger (kippo#31). contract is set from the nested route."""

    class Meta:
        model = KippoProjectBillingEntry
        fields = [
            "id",
            "contract",
            "billing_date",
            "amount",
            "is_manual",
            "is_received",
            "received_datetime",
            "received_by",
            "note",
            "created_datetime",
            "updated_datetime",
        ]
        # contract comes from the URL (nested under projects/); received_datetime/received_by are
        # auto-managed by the model save() (stamped when is_received is set, cleared when unset).
        read_only_fields = ["id", "contract", "received_datetime", "received_by", "created_datetime", "updated_datetime"]


class BillingListEntrySerializer(serializers.ModelSerializer):
    """Flat, read-only billing-ledger row for the cross-project 請求一覧 (billing list) UI.

    One row per ``KippoProjectBillingEntry``, denormalized with the project / contract / customer
    display fields the 請求一覧 needs so the UI can render, filter and sum without a second lookup
    per row. Read-only — the ledger is edited through the nested per-project billing-entries endpoint.
    """

    project_id = serializers.UUIDField(source="contract.project.id", read_only=True)
    project_name = serializers.CharField(source="contract.project.name", read_only=True)
    organization_name = serializers.CharField(source="contract.project.organization.name", read_only=True)
    project_phase = serializers.CharField(source="contract.project.phase", read_only=True)
    project_actual_date = serializers.DateField(source="contract.project.actual_date", read_only=True, allow_null=True)
    # 請求先: the contract's billed_to (may be null if later cleared); customer_name is the project's
    # 顧客 fallback so the UI can render a 請求先 even when billed_to is unset.
    billed_to_name = serializers.CharField(source="contract.billed_to.name", read_only=True, allow_null=True)
    customer_name = serializers.CharField(source="contract.project.customer.name", read_only=True, allow_null=True)
    billing_type = serializers.CharField(source="contract.billing_type", read_only=True)
    pricing_basis = serializers.CharField(source="contract.pricing_basis", read_only=True)
    contract_total_amount = serializers.DecimalField(source="contract.total_amount", max_digits=12, decimal_places=0, read_only=True, allow_null=True)
    contract_end_date = serializers.DateField(source="contract.end_date", read_only=True, allow_null=True)
    received_by_username = serializers.CharField(source="received_by.username", read_only=True, allow_null=True)

    class Meta:
        model = KippoProjectBillingEntry
        fields = [
            "id",
            "billing_date",
            "amount",
            "is_manual",
            "is_received",
            "received_datetime",
            "received_by_username",
            "note",
            "project_id",
            "project_name",
            "organization_name",
            "project_phase",
            "project_actual_date",
            "billed_to_name",
            "customer_name",
            "billing_type",
            "pricing_basis",
            "contract_total_amount",
            "contract_end_date",
        ]
        read_only_fields = fields


@extend_schema_field(OpenApiTypes.STR)
class ProjectCategoryKeyField(serializers.Field):
    """Read/write the KippoProject.category as its KEY string.

    Read → the FK's key. Write → passes the raw key string through unresolved; the FK is resolved
    against the project's own organization in ``KippoProjectSerializer.validate`` (kippo#49
    copy-on-create — each org owns its category set, so a key can't be resolved against the global
    template alone).
    """

    default_error_messages = {"invalid": _("A category key must be a string.")}

    def to_representation(self, value: "KippoProjectOrganizationCategory | None") -> "str | None":
        return value.key if value else None

    def to_internal_value(self, data: object) -> str:
        if not isinstance(data, str):
            self.fail("invalid")
        return data


def build_project_batch_context(projects: Sequence[KippoProject]) -> dict:
    """Effort / survey / holiday lookups for `projects`, shared through the KippoProjectSerializer context.

    The list view builds it once per page; a detail or write response builds it for its one project
    (see KippoProjectSerializer._batch_context), so the derived fields have a single code path.

    Returns keys `project_effort_totals`, `project_user_efforts`, `project_survey_completed_users`
    and `public_holidays_by_country`. A project absent from `project_effort_totals` has no logged
    effort (matches KippoProject.get_total_effort() returning None).
    """
    project_ids = [project.id for project in projects]
    if not project_ids:
        return {}

    # One grouped ProjectWeeklyEffort aggregate for all projects.
    project_user_efforts: dict = {}
    project_effort_totals: dict = {}
    effort_rows = (
        ProjectWeeklyEffort.objects.filter(project_id__in=project_ids)
        .values("project_id", "user__id", "user__username", "user__first_name", "user__last_name")
        .annotate(user_hours=Sum("hours"))
    )
    for row in effort_rows:
        project_id = row.pop("project_id")
        project_user_efforts.setdefault(project_id, []).append(row)
        project_effort_totals[project_id] = (project_effort_totals.get(project_id) or 0) + (row["user_hours"] or 0)

    # Survey completions per project.
    project_survey_completed_users: dict = {}
    for project_id, created_by_id in KippoProjectUserStatisfactionResult.objects.filter(project_id__in=project_ids).values_list(
        "project_id", "created_by_id"
    ):
        project_survey_completed_users.setdefault(project_id, set()).add(created_by_id)

    return {
        "project_effort_totals": project_effort_totals,
        "project_user_efforts": project_user_efforts,
        "project_survey_completed_users": project_survey_completed_users,
        # Org public holidays fetched once per distinct country.
        "public_holidays_by_country": public_holidays_by_country(project.organization.default_holiday_country_id for project in projects),
    }


class KippoProjectSerializer(serializers.ModelSerializer):
    """Serializer for KippoProject model."""

    organization_name = serializers.CharField(source="organization.name", read_only=True)
    project_manager_username = serializers.CharField(source="project_manager.username", read_only=True, allow_null=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, allow_null=True)
    # 契約書フォルダURL (kippo#34 / T04): the linked customer's document_url, shown read-only on the
    # project (edited via the customer). Lets the project create/edit form display the contract folder.
    customer_document_url = serializers.URLField(source="customer.document_url", read_only=True, allow_null=True)
    # Human-readable project status label for `phase` (kippo#37 / T10). `phase` stays the editable key.
    phase_display = serializers.CharField(source="get_phase_display", read_only=True)
    # confidence (確度) defaults to the phase-derived value but is directly settable here for manual
    # override (the model field is editable=False, so it is declared explicitly to make it writable).
    # A set value is preserved by KippoProject.save() until phase changes; changing phase in the same
    # update re-derives confidence from the new phase and ignores any confidence sent (kippo#36 / T09).
    confidence = serializers.IntegerField(min_value=0, max_value=100, required=False)
    # category is a FK to KippoProjectOrganizationCategory; expose/accept it as the category key
    # string for API backward-compatibility. Writes resolve against the project's OWN organization's
    # categories in validate() (kippo#49 copy-on-create), falling back to the global template.
    category = ProjectCategoryKeyField(
        required=False,
        help_text="Project category key (e.g. 'ai-development', 'other', 'non-project').",
    )
    # Human-readable category label for the list/detail view (kippo#39 / T14); `category` stays the key.
    category_label = serializers.CharField(source="category.label", read_only=True, allow_null=True)
    # Human-readable lead_source (リード) label; `lead_source` stays the editable key. Blank when unset.
    lead_source_display = serializers.CharField(source="get_lead_source_display", read_only=True)
    # 請求方法 — the project's billing type as a one-element list (kippo#39 / T14). Read-only;
    # the billing method lives on KippoProjectContract (OneToOne) since kippo#31.
    billing_types = serializers.ListField(child=serializers.CharField(), read_only=True)
    # Planned per-month billing schedule for monthly-billing projects (kippo#39 / T15) — lets the
    # list render one row per month (月額は契約期間内毎月表示). Empty for non-monthly projects.
    monthly_billing_schedule = serializers.SerializerMethodField()
    # Derived revenue figures (kippo#32 / T13). Read-only — sourced from the contract + billing
    # ledger (kippo#31), so they never drift from the underlying records.
    total_revenue = serializers.DecimalField(max_digits=12, decimal_places=0, read_only=True)
    contract_amount = serializers.DecimalField(max_digits=12, decimal_places=0, read_only=True)
    allocated_effort_hours = serializers.SerializerMethodField()
    assignment_rates = serializers.SerializerMethodField()
    has_requirements = serializers.SerializerMethodField()
    projectstatus_display = serializers.SerializerMethodField()
    latest_comment = serializers.SerializerMethodField()
    # フェーズ / 確度 change log, newest first (kippo#79). Read-only: KippoProject.save() writes the rows,
    # so a PATCH that changes phase or confidence returns the row it just added.
    phase_confidence_changes = serializers.SerializerMethodField()
    weekly_effort_users = serializers.SerializerMethodField()
    survey_users = serializers.SerializerMethodField()
    github_repositories = serializers.SerializerMethodField()
    docbase_tag = CommaSeparatedField(max_length=255, allow_blank=True, required=False)
    columnset = serializers.PrimaryKeyRelatedField(
        queryset=ProjectColumnSet.objects.all(),
        required=False,
        help_text="ProjectColumnSet for this project. Defaults to the organization's default columnset when omitted.",
    )
    # parent_project (親プロジェクト) — original project for continuation projects (admin parity). Writable
    # FK; a cross-org or self-referencing parent is rejected in validate(). parent_project_name is the
    # read-only label so clients can render the selection without a second lookup.
    parent_project = serializers.PrimaryKeyRelatedField(
        queryset=KippoProject.objects.all(),
        required=False,
        allow_null=True,
        help_text="Original (parent) project for continuation projects.",
    )
    parent_project_name = serializers.CharField(source="parent_project.name", read_only=True, allow_null=True)
    # MTG calendar template URL + dsearch tag — read-only, mirroring the admin's
    # meeting_calendar_url_field / meeting_description_tag_field display methods.
    meeting_calendar_url = serializers.SerializerMethodField()
    meeting_description_tag = serializers.SerializerMethodField()
    # 顧客アンケートURL — the org's google-form survey link pre-populated with this project id,
    # mirroring the admin's get_projectsurvey_display_url column. Blank when the organization has
    # no survey form configured.
    projectsurvey_url = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # 顧客 choices: only the request user's organizations' customers (gap #1). The stricter
        # "must belong to the project's organization" rule is enforced in validate().
        _restrict_customer_field_to_user_organizations(self, "customer")

    class Meta:
        model = KippoProject
        fields = [
            "id",
            "organization",
            "organization_name",
            "customer",
            "customer_name",
            "customer_document_url",
            "name",
            "slug",
            "columnset",
            "phase",
            "phase_display",
            "confidence",
            "category",
            "category_label",
            "lead_source",
            "lead_source_display",
            "billing_types",
            "monthly_billing_schedule",
            "slack_channel_name",
            "slack_notification_channel_name",
            "enable_cost_report",
            "project_manager",
            "project_manager_username",
            "parent_project",
            "parent_project_name",
            "is_closed",
            "closed_datetime",
            "display_as_active",
            "display_in_project_report",
            "github_project_html_url",
            "github_project_api_nodeid",
            "github_repositories",
            "allocated_staff_days",
            "allocated_effort_hours",
            "start_date",
            "target_date",
            "actual_date",
            "total_revenue",
            "contract_amount",
            "document_folder_url",
            "docbase_tag",
            "problem_definition",
            "meeting_calendar_url",
            "meeting_description_tag",
            "projectsurvey_url",
            "survey_issued",
            "assignment_rates",
            "has_requirements",
            "projectstatus_display",
            "latest_comment",
            "phase_confidence_changes",
            "weekly_effort_users",
            "survey_users",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "slug",
            "organization_name",
            "customer_name",
            "project_manager_username",
            "parent_project_name",  # read-only label for the parent_project FK
            "meeting_calendar_url",  # MTG calendar template URL (admin parity)
            "meeting_description_tag",  # dsearch tag for meeting minutes (admin parity)
            "projectsurvey_url",  # 顧客アンケートURL (admin parity)
            "customer_document_url",  # linked customer's contract-folder URL (kippo#34 / T04)
            "phase_display",  # human-readable status label (kippo#37 / T10)
            "category_label",  # human-readable category label (kippo#39 / T14)
            "billing_types",  # distinct contract billing types (kippo#39 / T14)
            "monthly_billing_schedule",  # planned per-month schedule (kippo#39 / T15)
            "total_revenue",  # ledger-derived (kippo#32 / T13)
            "contract_amount",  # contract-derived (kippo#32 / T13)
            "closed_datetime",
            "allocated_effort_hours",
            "assignment_rates",
            "has_requirements",
            "projectstatus_display",
            "latest_comment",
            "phase_confidence_changes",
            "weekly_effort_users",
            "survey_users",
            "github_repositories",
            "created_datetime",
            "updated_datetime",
        ]

    def validate(self, attrs: dict) -> dict:
        """Resolve/validate `columnset` against the project's organization.

        - explicit columnset must be organization-specific (same org) or global (org-null)
        - on create without a columnset, apply `organization.get_default_columnset()`
        """
        attrs = super().validate(attrs)
        organization = attrs.get("organization") or getattr(self.instance, "organization", None)
        self._validate_customer_organization(attrs, organization)
        self._resolve_category(attrs, organization)
        columnset = attrs.get("columnset")
        # Re-parenting (organization changes) without a new columnset must re-check the
        # project's *existing* columnset against the new org — otherwise a stale cross-org
        # columnset silently persists.
        if columnset is None and self.instance is not None and "organization" in attrs:
            columnset = self.instance.columnset
        if columnset is not None:
            if organization is not None and columnset.organization_id not in (None, organization.id):
                raise serializers.ValidationError(
                    {"columnset": "Selected columnset must belong to the project's organization (or be a shared/global columnset)."}
                )
        elif self.instance is None:
            columnset = organization.get_default_columnset() if organization is not None else None
            if columnset is None:
                raise serializers.ValidationError(
                    {"columnset": "No columnset provided and no default columnset is configured for this organization."}
                )
            attrs["columnset"] = columnset

        self._validate_parent_project(attrs, organization)
        self._validate_enable_cost_report(attrs)

        # Required-field validation at project registration (kippo#40 / T19; slimmed for the
        # contract-driven flow). Create-only — edits of existing rows (and existing data) are
        # unaffected. name/organization are NOT NULL and category/phase carry model defaults, so
        # registration only additionally requires customer + start_date; everything else (PM,
        # target_date, the contract, estimates) is added on a later edit.
        if self.instance is None:
            required_at_registration = ("customer", "start_date")
            missing = {field: "This field is required at project registration." for field in required_at_registration if not attrs.get(field)}
            if missing:
                raise serializers.ValidationError(missing)

        self._validate_contract_synced_dates(attrs)
        self._validate_under_contract_phase(attrs)
        return attrs

    def _validate_customer_organization(self, attrs: dict, organization: "KippoOrganization | None") -> None:
        """顧客 must belong to the project's organization (requirement: a project's customer is one of
        its own org's customers). A blank/omitted customer is left alone. The field queryset is already
        bounded to the user's organizations (__init__); this enforces the exact project-org match.
        """
        customer = attrs.get("customer")
        if customer is not None and organization is not None and customer.organization_id != organization.id:
            raise serializers.ValidationError({"customer": _("Customer must belong to the project's organization.")})

    def _resolve_category(self, attrs: dict, organization: "KippoOrganization | None") -> None:
        """Resolve the incoming category KEY string to the project's org category FK (kippo#49).

        Prefer the project organization's OWN category with that key (copy-on-create means each org
        owns its set, including org-custom keys like 'sunx'); fall back to the global template row so
        a pre-copy org / global-only key still resolves (KippoProject.save() then remaps a resolved
        global to the org's copy — a project never references a global). An unresolvable key is a 400,
        not a 500. Omitted category (not in attrs) leaves the model default untouched.
        """
        category_key = attrs.get("category")
        if not isinstance(category_key, str):
            return
        category = None
        if organization is not None:
            category = KippoProjectOrganizationCategory.objects.filter(organization=organization, key=category_key).first()
        if category is None:
            category = KippoProjectOrganizationCategory.objects.filter(organization__isnull=True, key=category_key).first()
        if category is None:
            raise serializers.ValidationError({"category": _("Unknown category key '%(key)s' for this organization.") % {"key": category_key}})
        attrs["category"] = category

    def _validate_contract_synced_dates(self, attrs: dict) -> None:
        """Once a contract has a period, that period is the single source of truth — the project's
        start_date/target_date are synced mirrors (KippoProjectContract._sync_project_period). Reject
        a project-side value that diverges from the contract period so date edits go through the
        contract endpoint; a value equal to the contract's is a no-op and stays valid.

        Compared against the CONTRACT period (not the stored project value): a project whose stored
        date drifted from the contract can still be reconciled to the contract value, and a value
        matching a stale stored date is no longer wrongly accepted. A blank contract date is not
        managed, so that project field stays directly editable (e.g. a blank-period contract).
        """
        if self.instance is None:
            return
        contract = self.instance.get_contract()
        if contract is None:
            return
        errors = {}
        for field, contract_value in (("start_date", contract.start_date), ("target_date", contract.end_date)):
            if contract_value and field in attrs and attrs[field] != contract_value:
                errors[field] = "This project has a contract; its dates are managed by the contract period (update via the contract endpoint)."
        if errors:
            raise serializers.ValidationError(errors)

    def _validate_under_contract_phase(self, attrs: dict) -> None:
        """契約(稼働中) requires the contract (with its period) to exist first — mirrors
        KippoProject.clean(). The API cannot attach a contract at project-create, so a create
        directly in this phase is rejected; create in an earlier phase, add the contract, then
        update the phase.
        """
        # Transition-only: gate only a *move into* 契約(稼働中). A row already persisted in the phase
        # (e.g. legacy rows created before contracts existed) stays editable — otherwise any PATCH
        # (even name-only, since the SPA always sends phase) would re-fire the gate and 400.
        stored_phase = getattr(self.instance, "phase", None)
        incoming_phase = attrs.get("phase", stored_phase)
        if incoming_phase != PHASE_UNDER_CONTRACT or stored_phase == PHASE_UNDER_CONTRACT:
            return
        contract = self.instance.get_contract() if self.instance is not None else None
        if not (contract and contract.has_complete_period()):
            raise serializers.ValidationError({"phase": UNDER_CONTRACT_REQUIRES_CONTRACT_MSG})

    def _validate_parent_project(self, attrs: dict, organization: "KippoOrganization | None") -> None:
        """parent_project (continuation) must be same-org and not the project itself (admin parity)."""
        parent_project = attrs.get("parent_project")
        if parent_project is None:
            return
        if self.instance is not None and parent_project.id == self.instance.id:
            raise serializers.ValidationError({"parent_project": "A project cannot be its own parent project."})
        if organization is not None and parent_project.organization_id != organization.id:
            raise serializers.ValidationError({"parent_project": "Parent project must belong to the project's organization."})

    def _validate_enable_cost_report(self, attrs: dict) -> None:
        """enable_cost_report requires a slack_channel_name (mirrors KippoProject.clean()). Falls back
        to the stored values for fields absent from this (partial) update.
        """
        enable_cost_report = attrs.get("enable_cost_report")
        if enable_cost_report is None and self.instance is not None:
            enable_cost_report = self.instance.enable_cost_report
        if not enable_cost_report:
            return
        slack_channel_name = attrs.get("slack_channel_name")
        if slack_channel_name is None and self.instance is not None:
            slack_channel_name = self.instance.slack_channel_name
        if not slack_channel_name:
            raise serializers.ValidationError({"enable_cost_report": "slack_channel_name is required when enable_cost_report is True."})

    @extend_schema_field(serializers.CharField())
    def get_meeting_calendar_url(self, obj: KippoProject) -> str:
        """Google Calendar event-template URL pre-filled for this project (admin parity)."""
        return obj.get_meeting_calendar_template_url()

    @extend_schema_field(serializers.CharField())
    def get_meeting_description_tag(self, obj: KippoProject) -> str:
        """Dsearch sentinel tag embedding the project id, for meeting-minutes discovery (admin parity)."""
        return obj.get_dsearch_tag()

    @extend_schema_field(serializers.CharField())
    def get_projectsurvey_url(self, obj: KippoProject) -> str:
        """顧客アンケートURL — org survey form pre-populated with the project id (admin parity).

        Empty string when the organization has no survey form configured, matching
        KippoProject.get_projectsurvey_url() and the admin column's blank cell.
        """
        return obj.get_projectsurvey_url()

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_allocated_effort_hours(self, obj: KippoProject) -> float | None:
        """予算工数 — delegated to KippoProject.allocated_effort_hours.

        Previously computed here as `allocated_staff_days × settings.DAY_WORKHOURS` — the only
        effort calculation in the codebase that read that setting instead of the project
        organization's own `day_workhours`, so this field disagreed with
        `projectstatus_display.allocated_effort_hours` for any organization whose workday is not 7h.
        (The setting remains, but only as the github issue-label fallback in `commons.github`.)
        Delegating also picks up the 納品/固定 contract estimate, keeping the two fields consistent
        for a project with no 割当工数(人日) entered.
        """
        return obj.allocated_effort_hours

    @extend_schema_field(GithubRepositoryInlineSerializer(many=True))
    def get_github_repositories(self, obj: KippoProject) -> list[dict]:
        return [{"repository_url": repo.html_url} for repo in obj.github_repositories.all()]

    @extend_schema_field(MonthlyBillingScheduleEntrySerializer(many=True))
    def get_monthly_billing_schedule(self, obj: KippoProject) -> list[dict]:
        # str(amount) to match the string rendering of the model's DecimalField revenue fields
        return [{"month": month.isoformat(), "amount": str(amount)} for month, amount in obj.monthly_billing_schedule]

    @extend_schema_field(ProjectAssignmentRateInlineSerializer(many=True))
    def get_assignment_rates(self, obj: KippoProject) -> list[dict]:
        """Return assignment rates for all roles, using defaults for missing entries."""
        existing_rates = {rate.role: rate for rate in obj.assignment_rates.all()}
        rates = []
        for role in ProjectRoles:
            if role.value in existing_rates:
                rate = existing_rates[role.value]
                rates.append(
                    {
                        "role": role.value,
                        "rate_per_day": rate.rate_per_day,
                        "is_default": False,
                    }
                )
            else:
                rates.append(
                    {
                        "role": role.value,
                        "rate_per_day": settings.DEFAULT_PROJECT_DAILY_RATE,
                        "is_default": True,
                    }
                )
        return rates

    @extend_schema_field(serializers.BooleanField())
    def get_has_requirements(self, obj: KippoProject) -> bool:
        """Check if the project has any problem definitions."""
        annotated = getattr(obj, "has_requirements_annotated", None)
        if annotated is not None:
            return annotated

        from requirements.models import ProjectProblemDefinition

        return ProjectProblemDefinition.objects.filter(project=obj).exists()

    def _batch_context(self, obj: KippoProject) -> dict:
        """The effort / survey / holiday lookups covering `obj` (see build_project_batch_context).

        The list view supplies them for the whole page through the serializer context. Otherwise
        (detail, create, update) they are built for this one project and kept for the remaining fields.
        """
        if "project_effort_totals" in self.context:
            return self.context
        single_project_contexts = self.__dict__.setdefault("_single_project_contexts", {})
        if obj.id not in single_project_contexts:
            single_project_contexts[obj.id] = build_project_batch_context([obj])
        return single_project_contexts[obj.id]

    @extend_schema_field(ProjectProgressStatusInlineSerializer(allow_null=True))
    def get_projectstatus_display(self, obj: KippoProject) -> dict | None:
        """Get the project progress status display values."""
        batch = self._batch_context(obj)
        project_progress_status: ProjectProgressStatus = obj.get_projectprogressstatus_values(
            total_effort=batch["project_effort_totals"].get(obj.id),
            holidays=batch["public_holidays_by_country"].get(obj.organization.default_holiday_country_id, set()),
        )
        if project_progress_status.allocated_effort_hours is None:
            return None
        return {
            "current_effort_hours": project_progress_status.current_effort_hours,
            "expected_effort_hours": project_progress_status.expected_effort_hours,
            "allocated_effort_hours": project_progress_status.allocated_effort_hours,
            "is_estimated_allocated_effort_hours": project_progress_status.is_estimated_allocated_effort_hours,
            "difference_percentage": project_progress_status.get_difference_percentage(),
            "level": effort_level(project_progress_status),
        }

    @extend_schema_field(KippoProjectPhaseConfidenceChangeSerializer(many=True))
    def get_phase_confidence_changes(self, obj: KippoProject) -> list[dict]:
        """The project's フェーズ / 確度 changes, newest first."""
        # The viewset prefetches the log with changed_by. A response built without it (create,
        # or update, which clears the prefetch cache) reads the log here in one query.
        if "phase_confidence_changes" in getattr(obj, "_prefetched_objects_cache", {}):
            changes = obj.phase_confidence_changes.all()
        else:
            changes = obj.phase_confidence_changes.select_related("changed_by")
        return KippoProjectPhaseConfidenceChangeSerializer(changes, many=True).data

    @extend_schema_field(LatestCommentInlineSerializer(allow_null=True))
    def get_latest_comment(self, obj: KippoProject) -> dict | None:
        """Get the latest KippoProjectStatus comment with commentor info."""
        # The viewset prefetches statuses ordered newest-first into `_prefetched_latest_statuses`
        # (with created_by select_related) so the list endpoint avoids a per-row `.latest()` query.
        prefetched = getattr(obj, "_prefetched_latest_statuses", None)
        if prefetched is not None:
            latest_status = prefetched[0] if prefetched else None
        else:
            latest_status = obj.get_latest_kippoprojectstatus()
        if latest_status:
            created_by = latest_status.created_by
            return {
                "comment": latest_status.comment,
                "created_by_username": created_by.username if created_by else None,
                "created_by_display_name": _user_display_name(created_by),
                "created_datetime": latest_status.created_datetime,
            }
        return None

    def _user_effort_shares(self, obj: KippoProject) -> list[dict]:
        """Each user's hours and share of the project's logged effort. Empty when nothing is logged."""
        batch = self._batch_context(obj)
        total_hours = batch["project_effort_totals"].get(obj.id) or 0
        if total_hours == 0:
            return []
        return user_effort_shares(batch["project_user_efforts"].get(obj.id, []), total_hours)

    @extend_schema_field(WeeklyEffortUserInlineSerializer(many=True))
    def get_weekly_effort_users(self, obj: KippoProject) -> list[dict]:
        """Get list of users with their weekly effort percentages for this project."""
        shares = sorted(self._user_effort_shares(obj), key=lambda share: share["hours"], reverse=True)
        return [{**share, "percentage": round(share["percentage"], 2)} for share in shares]

    @extend_schema_field(SurveyUserInlineSerializer(many=True))
    def get_survey_users(self, obj: KippoProject) -> list[dict]:
        """Get list of users with >3% effort who should complete the retrospective survey.

        Returns users sorted alphabetically by username with their survey completion status.
        Only includes users with effort percentage > 3%.
        """
        completed_user_ids = self._batch_context(obj)["project_survey_completed_users"].get(obj.id, set())
        survey_users = [
            {
                "user_id": share["user_id"],
                "username": share["username"],
                "display_name": share["display_name"],
                "percentage": round(share["percentage"], 2),
                "survey_completed": share["user_id"] in completed_user_ids,
            }
            for share in self._user_effort_shares(obj)
            if share["percentage"] > SURVEY_EFFORT_THRESHOLD_PERCENTAGE
        ]
        return sorted(survey_users, key=lambda survey_user: survey_user["username"])


class ProjectWeeklyEffortSerializer(serializers.ModelSerializer):
    """Serializer for ProjectWeeklyEffort model.

    The `user` field defaults to the current authenticated user on create.
    Superusers can optionally specify a different user.
    """

    project_name = serializers.CharField(source="project.name", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True, allow_null=True)
    user_display_name = serializers.SerializerMethodField()
    user = serializers.PrimaryKeyRelatedField(
        queryset=KippoUser.objects.all(),
        required=False,
        allow_null=True,
        default=serializers.CurrentUserDefault(),
    )
    # Effort cannot be negative and cannot exceed the hours in a week (7 * 24).
    # The interactive UI/Slack flows already block negatives; this guards the
    # direct-API and admin paths that previously accepted them.
    hours = serializers.IntegerField(min_value=0, max_value=7 * 24)
    is_closed = serializers.SerializerMethodField()

    class Meta:
        model = ProjectWeeklyEffort
        fields = [
            "id",
            "week_start",
            "project",
            "project_name",
            "user",
            "user_username",
            "user_display_name",
            "hours",
            "is_closed",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "project_name",
            "user_username",
            "user_display_name",
            "is_closed",
            "created_datetime",
            "updated_datetime",
        ]

    @extend_schema_field(serializers.BooleanField())
    def get_is_closed(self, obj: ProjectWeeklyEffort) -> bool:
        """週間稼働の締め判定 (T17): 締め日時を過ぎていて有効なアンロックが無い場合 True (編集不可)。"""
        now = timezone.now()
        if now < obj.close_datetime:
            return False
        return (obj.project.organization_id, obj.user_id, obj.week_start) not in self._active_unlock_keys(now)

    def _active_unlock_keys(self, now: datetime.datetime) -> set:
        # one query per serialization (instead of one EXISTS per closed row in list responses);
        # the active-unlock predicate is defined once on the model (kippo#33 / #5).
        if not hasattr(self, "_unlock_keys_cache"):
            self._unlock_keys_cache = ProjectWeeklyEffortUnlock.active_unlock_keys(now)
        return self._unlock_keys_cache

    def validate(self, attrs: dict) -> dict:
        """Reject create/update of weekly effort whose week is closed (T17), unless an
        admin unlock is active for the (organization, user, week_start) (T18).
        Superusers bypass the check.
        """
        request = self.context.get("request")
        request_user = getattr(request, "user", None)
        if request_user is not None and request_user.is_superuser:
            return attrs

        # an existing (locked) entry may not be modified
        if self.instance and self.instance.is_closed():
            raise serializers.ValidationError({"week_start": WEEKLY_EFFORT_CLOSED_MESSAGE}, code="weekly_effort_closed")

        # the target (new) values may not land in a closed week
        project = attrs.get("project") or (self.instance.project if self.instance else None)
        week_start = attrs.get("week_start") or (self.instance.week_start if self.instance else previous_week_startdate())
        effort_user = attrs.get("user") or (self.instance.user if self.instance else request_user)
        if project and effort_user and project.organization.is_weeklyeffort_closed(effort_user, week_start):
            raise serializers.ValidationError({"week_start": WEEKLY_EFFORT_CLOSED_MESSAGE}, code="weekly_effort_closed")
        return attrs

    @extend_schema_field(serializers.CharField())
    def get_user_display_name(self, obj: ProjectWeeklyEffort) -> str:
        """Get the user's display name."""
        user = obj.user
        if hasattr(user, "get_display_name"):
            return user.get_display_name()
        return f"{user.first_name} {user.last_name}".strip() or user.username


class ProjectWeeklyEffortUnlockSerializer(serializers.ModelSerializer):
    """週間稼働アンロックの申請/承認シリアライザ (kippo#33 / T18).

    Create = 申請: ユーザは `organization` + `week_start` + `reason` を送信する。`user` は申請者本人に固定され、
    承認関連フィールドは read-only。承認は viewset の `approve` アクション (組織admin限定) で行う。
    """

    user = serializers.PrimaryKeyRelatedField(read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    approved_by_username = serializers.CharField(source="approved_by.username", read_only=True, allow_null=True)
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = ProjectWeeklyEffortUnlock
        fields = [
            "id",
            "organization",
            "user",
            "user_username",
            "week_start",
            "reason",
            "approved_by",
            "approved_by_username",
            "approved_datetime",
            "expires_datetime",
            "is_active",
            "created_by",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "user",
            "user_username",
            "approved_by",
            "approved_by_username",
            "approved_datetime",
            "expires_datetime",
            "is_active",
            "created_by",
            "created_datetime",
            "updated_datetime",
        ]

    @extend_schema_field(serializers.BooleanField())
    def get_is_active(self, obj: ProjectWeeklyEffortUnlock) -> bool:
        """承認済みかつ再ロック期限前なら True (現在編集可能)。"""
        return obj.is_active()

    def validate_organization(self, organization: KippoOrganization) -> KippoOrganization:
        """申請者は所属する組織のアンロックのみ申請できる (superuser を除く)。"""
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and not user_can_access_organization(user, organization.pk):
            raise serializers.ValidationError("所属していない組織のアンロックは申請できません。")
        return organization

    def validate(self, attrs: dict) -> dict:
        """Reject a duplicate request for the same (organization, user, week_start) with a 400
        instead of letting the unique_together constraint raise an IntegrityError (500). `user`
        is read-only (set to the requester in the viewset), so DRF cannot derive this validator.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not self.instance and user is not None:
            exists = ProjectWeeklyEffortUnlock.objects.filter(
                organization=attrs.get("organization"), user=user, week_start=attrs.get("week_start")
            ).exists()
            if exists:
                raise serializers.ValidationError({"week_start": "この週のアンロックは既に申請済みです。"}, code="unlock_exists")
        return attrs


@extend_schema_serializer(deprecate_fields=["is_confirmed"])
class ProjectMonthlyAssignmentSerializer(serializers.ModelSerializer):
    """Serializer for ProjectMonthlyAssignment model.

    `is_confirmed` (確定) is deprecated (kippo#69 D3): kept for API compatibility only, carries no
    behaviour, and is no longer gated or used to trigger auto-extend.
    """

    is_confirmed = serializers.BooleanField(
        required=False,
        help_text="Deprecated (kippo#69): no longer used. Kept for API compatibility; defaults to false.",
    )
    project_name = serializers.CharField(source="project.name", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    user_display_name = serializers.SerializerMethodField()
    user_github_login = serializers.CharField(source="user.github_login", read_only=True)
    user_slack_username = serializers.SerializerMethodField()
    user_slack_image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProjectMonthlyAssignment
        fields = [
            "id",
            "project",
            "project_name",
            "user",
            "user_username",
            "user_display_name",
            "user_github_login",
            "user_slack_username",
            "user_slack_image_url",
            "month",
            "percentage",
            "role",
            "is_confirmed",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "project_name",
            "user_username",
            "user_display_name",
            "user_github_login",
            "user_slack_username",
            "user_slack_image_url",
            "created_datetime",
            "updated_datetime",
        ]

    def validate(self, attrs: dict) -> dict:
        """O2 (kippo#75): a row for a new (project, user) pair needs a ProjectParticipant — 400 instead of the
        model's full_clean() error surfacing as a 500.
        """
        attrs = super().validate(attrs)
        project = attrs.get("project") or getattr(self.instance, "project", None)
        user = attrs.get("user") or getattr(self.instance, "user", None)
        pair_changed = self.instance is None or (project, user) != (self.instance.project, self.instance.user)
        if project and user and pair_changed and not ProjectParticipant.objects.filter(project=project, user=user).exists():
            raise serializers.ValidationError(
                {"user": f"User '{user.username}' is not a participant of project '{project.name}'; add them as a project participant first."},
                code="participant_required",
            )
        return attrs

    def _get_user_organization_membership(self, obj: ProjectMonthlyAssignment) -> "OrganizationMembership | None":
        """Get the user's OrganizationMembership for the project's organization.

        Memoized per serializer instance (both slack fields call this per row) and resolved from the
        viewset's prefetched `user__organizationmembership_set` when present, falling back to a direct
        query for standalone/single-object use.
        """
        organization_id = obj.project.organization_id
        cache = self.__dict__.setdefault("_membership_cache", {})
        key = (obj.user_id, organization_id)
        if key in cache:
            return cache[key]

        # `.all()` uses the prefetched cache when the viewset prefetched it (no query); otherwise it
        # issues one query for the user's memberships, which is then filtered in Python.
        membership = next((m for m in obj.user.organizationmembership_set.all() if m.organization_id == organization_id), None)
        cache[key] = membership
        return membership

    @extend_schema_field(serializers.CharField())
    def get_user_display_name(self, obj: ProjectMonthlyAssignment) -> str:
        """Get the user's display name."""
        user = obj.user
        if hasattr(user, "display_name"):
            return user.display_name
        return f"{user.first_name} {user.last_name}".strip() or user.username

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_user_slack_username(self, obj: ProjectMonthlyAssignment) -> str | None:
        """Get the user's Slack username from their organization membership."""
        membership = self._get_user_organization_membership(obj)
        if membership:
            return membership.slack_username or None
        return None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_user_slack_image_url(self, obj: ProjectMonthlyAssignment) -> str | None:
        """Get the user's Slack image URL from their organization membership."""
        membership = self._get_user_organization_membership(obj)
        if membership:
            return membership.slack_image_url or None
        return None


class ProjectParticipantCreateSerializer(serializers.Serializer):
    """Request body for POST /api/projects/{id}/participants/ (kippo#72)."""

    user = serializers.PrimaryKeyRelatedField(queryset=KippoUser.objects.all())
    percentage = serializers.IntegerField(min_value=0)
    role = serializers.ChoiceField(choices=ProjectRoles.choices(), required=False, allow_blank=True, default="")


class ProjectParticipantSerializer(serializers.ModelSerializer):
    """A project participant (kippo#75): the parent of the user's monthly assignments on the project."""

    user_username = serializers.CharField(source="user.username", read_only=True)
    user_display_name = serializers.CharField(source="user.display_name", read_only=True)

    class Meta:
        model = ProjectParticipant
        fields = ["id", "project", "user", "user_username", "user_display_name", "percentage", "role", "created_datetime", "updated_datetime"]
        read_only_fields = fields


class ProjectParticipantResponseSerializer(serializers.Serializer):
    """Response body for POST /api/projects/{id}/participants/ (kippo#72)."""

    created = ProjectMonthlyAssignmentSerializer(many=True)
    skipped_months = serializers.ListField(child=serializers.DateField())


class ProjectMonthlyCostSerializer(serializers.ModelSerializer):
    """Serializer for ProjectMonthlyCost model."""

    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = ProjectMonthlyCost
        fields = [
            "id",
            "project",
            "project_name",
            "month",
            "service",
            "cost",
            "currency",
            "itemized_cost",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "project_name",
            "created_datetime",
            "updated_datetime",
        ]


class KippoProjectUserStatisfactionResultSerializer(serializers.ModelSerializer):
    """Serializer for KippoProjectUserStatisfactionResult model (振り返り従業員アンケート).

    The `created_by` field is auto-set to the current authenticated user on create.
    """

    project_name = serializers.CharField(source="project.name", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    created_by_display_name = serializers.SerializerMethodField()

    class Meta:
        model = KippoProjectUserStatisfactionResult
        fields = [
            "id",
            "project",
            "project_name",
            "fullfillment_score",
            "growth_score",
            "created_by",
            "created_by_username",
            "created_by_display_name",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "project_name",
            "created_by",
            "created_by_username",
            "created_by_display_name",
            "created_datetime",
            "updated_datetime",
        ]

    @extend_schema_field(serializers.CharField())
    def get_created_by_display_name(self, obj: KippoProjectUserStatisfactionResult) -> str:
        """Get the user's display name."""
        user = obj.created_by
        if user:
            first_name = user.first_name or ""
            last_name = user.last_name or ""
            return f"{first_name} {last_name}".strip() or user.username
        return ""
