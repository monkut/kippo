import datetime
from typing import TYPE_CHECKING

from accounts.models import KippoOrganization, KippoUser
from commons.fields import CommaSeparatedField
from commons.viewsets import organization_ids_for_user
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .definitions import (
    BILLING_TYPE_MONTHLY,
    FULL_CONFIDENCE_PERCENTAGE,
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
    KippoProjectUserStatisfactionResult,
    ProjectAssignmentRate,
    ProjectColumnSet,
    ProjectMonthlyAssignment,
    ProjectMonthlyCost,
    ProjectWeeklyEffort,
    ProjectWeeklyEffortUnlock,
)

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
    difference_percentage = serializers.FloatField(allow_null=True)


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


class ProjectAssignmentPatternMemberSerializer(serializers.Serializer):
    """One member of a suggested assignment pattern. Mirrors the Pydantic
    `ProjectAssignmentPatternMember` shape from `projects.definitions`.
    """

    user_id = serializers.UUIDField(help_text="KippoUser primary key.")
    is_past_member = serializers.BooleanField(help_text="True when the user already has a row on this project.")
    monthly_percentages = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="Per-month percentage allocation. Keys are first-of-month ISO dates ('YYYY-MM-01').",
    )


class ProjectAssignmentPatternConflictSerializer(serializers.Serializer):
    """An over-allocation conflict surfaced by the suggester for a (user × month) cell."""

    user_id = serializers.UUIDField()
    month = serializers.DateField(help_text="First-of-month ISO date.")
    reason = serializers.CharField()


class ProjectAssignmentPatternSerializer(serializers.Serializer):
    """A complete suggested project-assignment pattern. Mirrors the Pydantic
    `ProjectAssignmentPattern` shape from `projects.definitions`. Per kippo#231.

    `pattern_ids` carries every strategy key that produced this pattern after dedup
    (kippo#227 S3) — typically one entry, e.g. `['P1-max-reuse']`, but multiple when
    strategies converged on the same members + monthly_percentages.
    """

    pattern_ids = serializers.ListField(
        child=serializers.CharField(),
        help_text="Strategy keys that produced this pattern (one entry, or several after dedup).",
    )
    label = serializers.CharField(help_text="Human-readable label derived from pattern_ids.")
    estimated_completion = serializers.DateField(
        allow_null=True,
        help_text="Day-precision completion date; null when the pattern can't reach the allocated effort.",
    )
    infeasible = serializers.BooleanField(
        help_text="True when the pattern overshoots target_date or breaches the per-user 100% cap.",
    )
    conflicts = ProjectAssignmentPatternConflictSerializer(many=True)
    members = ProjectAssignmentPatternMemberSerializer(many=True)


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
    if field is None or user is None or getattr(user, "is_superuser", False) or not hasattr(user, "organizationmembership_set"):
        return
    # Reuse the request-scoped org-id cache when warm, else the LAZY membership queryset so Django
    # inlines it as a subquery (mirrors OrganizationFilterMixin.filter_by_organization). The field
    # queryset is only evaluated for write validation / browsable-API choices — never on a JSON GET —
    # so this adds no query to list/detail reads.
    user_organizations = getattr(user, "_organization_ids_cache", None)
    if user_organizations is None:
        user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
    field.queryset = field.queryset.filter(organization__in=user_organizations)


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
        """Calculate allocated effort in hours from staff days."""
        if obj.allocated_staff_days is not None:
            return obj.allocated_staff_days * settings.DAY_WORKHOURS
        return None

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

    @extend_schema_field(ProjectProgressStatusInlineSerializer(allow_null=True))
    def get_projectstatus_display(self, obj: KippoProject) -> dict | None:
        """Get the project progress status display values."""
        # In list context the effort total and org holidays are precomputed once per page
        # (see KippoProjectViewSet); inject them to avoid a per-row aggregate + PublicHoliday query.
        effort_totals = self.context.get("project_effort_totals")
        if effort_totals is not None:
            holidays = self.context["public_holidays_by_country"].get(obj.organization.default_holiday_country_id, set())
            project_progress_status: ProjectProgressStatus = obj.get_projectprogressstatus_values(
                total_effort=effort_totals.get(obj.id), holidays=holidays
            )
        else:
            project_progress_status = obj.get_projectprogressstatus_values()
        if project_progress_status.allocated_effort_hours is None:
            return None
        return {
            "current_effort_hours": project_progress_status.current_effort_hours,
            "expected_effort_hours": project_progress_status.expected_effort_hours,
            "allocated_effort_hours": project_progress_status.allocated_effort_hours,
            "difference_percentage": project_progress_status.get_difference_percentage(),
        }

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
            display_name = None
            username = None
            if created_by:
                username = created_by.username
                first_name = created_by.first_name or ""
                last_name = created_by.last_name or ""
                display_name = f"{first_name} {last_name}".strip() or username
            return {
                "comment": latest_status.comment,
                "created_by_username": username,
                "created_by_display_name": display_name,
                "created_datetime": latest_status.created_datetime,
            }
        return None

    def _effort_context_rows(self, obj: KippoProject) -> tuple[int, list[dict]] | None:
        """Return `(total_hours, user_effort_rows)` from the per-page batch context, or None.

        The viewset precomputes one grouped `ProjectWeeklyEffort` aggregate per page and stores it
        in serializer context, so the effort-derived fields share it instead of each re-scanning
        the table. Returns None in detail/retrieve (no batch) so callers fall back to per-object queries.
        """
        user_efforts_by_project = self.context.get("project_user_efforts")
        if user_efforts_by_project is None:
            return None
        total_hours = self.context["project_effort_totals"].get(obj.id) or 0
        return total_hours, user_efforts_by_project.get(obj.id, [])

    @extend_schema_field(WeeklyEffortUserInlineSerializer(many=True))
    def get_weekly_effort_users(self, obj: KippoProject) -> list[dict]:
        """Get list of users with their weekly effort percentages for this project."""
        batch = self._effort_context_rows(obj)
        if batch is not None:
            total_hours, user_efforts = batch
            if total_hours == 0:
                return []
            user_efforts = sorted(user_efforts, key=lambda e: e["user_hours"], reverse=True)
        else:
            # Get total hours for the project
            total_hours_result = ProjectWeeklyEffort.objects.filter(project=obj).aggregate(total=Sum("hours"))
            total_hours = total_hours_result["total"] or 0

            if total_hours == 0:
                return []

            # Get hours per user
            user_efforts = (
                ProjectWeeklyEffort.objects.filter(project=obj)
                .values("user__id", "user__username", "user__first_name", "user__last_name")
                .annotate(user_hours=Sum("hours"))
                .order_by("-user_hours")
            )

        result = []
        for effort in user_efforts:
            first_name = effort["user__first_name"] or ""
            last_name = effort["user__last_name"] or ""
            display_name = f"{first_name} {last_name}".strip() or effort["user__username"]
            user_hours = effort["user_hours"] or 0
            percentage = (user_hours / total_hours) * 100 if total_hours > 0 else 0

            result.append(
                {
                    "user_id": effort["user__id"],
                    "username": effort["user__username"],
                    "display_name": display_name,
                    "hours": user_hours,
                    "percentage": round(percentage, 2),
                }
            )
        return result

    @extend_schema_field(SurveyUserInlineSerializer(many=True))
    def get_survey_users(self, obj: KippoProject) -> list[dict]:
        """Get list of users with >3% effort who should complete the retrospective survey.

        Returns users sorted alphabetically by username with their survey completion status.
        Only includes users with effort percentage > 3%.
        """
        batch = self._effort_context_rows(obj)
        if batch is not None:
            total_hours, user_efforts = batch
            if total_hours == 0:
                return []
            completed_user_ids = self.context["project_survey_completed_users"].get(obj.id, set())
        else:
            # Get total hours for the project
            total_hours_result = ProjectWeeklyEffort.objects.filter(project=obj).aggregate(total=Sum("hours"))
            total_hours = total_hours_result["total"] or 0

            if total_hours == 0:
                return []

            # Get hours per user
            user_efforts = (
                ProjectWeeklyEffort.objects.filter(project=obj)
                .values("user__id", "user__username", "user__first_name", "user__last_name")
                .annotate(user_hours=Sum("hours"))
            )

            # Get users who have completed the survey for this project
            completed_user_ids = set(KippoProjectUserStatisfactionResult.objects.filter(project=obj).values_list("created_by_id", flat=True))

        result = []
        for effort in user_efforts:
            user_hours = effort["user_hours"] or 0
            percentage = (user_hours / total_hours) * 100 if total_hours > 0 else 0

            # Only include users with effort above threshold
            if percentage <= SURVEY_EFFORT_THRESHOLD_PERCENTAGE:
                continue

            first_name = effort["user__first_name"] or ""
            last_name = effort["user__last_name"] or ""
            display_name = f"{first_name} {last_name}".strip() or effort["user__username"]
            user_id = effort["user__id"]

            result.append(
                {
                    "user_id": user_id,
                    "username": effort["user__username"],
                    "display_name": display_name,
                    "percentage": round(percentage, 2),
                    "survey_completed": user_id in completed_user_ids,
                }
            )

        # Sort alphabetically by username
        result.sort(key=lambda x: x["username"])
        return result


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
        if user is not None and not user.is_superuser:
            member_org_ids = set(user.organizationmembership_set.values_list("organization_id", flat=True))
            if organization.pk not in member_org_ids:
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


class ProjectMonthlyAssignmentSerializer(serializers.ModelSerializer):
    """Serializer for ProjectMonthlyAssignment model."""

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

    def validate(self, attrs: dict) -> dict:
        """Reject *confirming* an assignment whose project confidence (確度) is not 100%.

        Only the unconfirmed → confirmed transition is gated (returns a 400 so the UI can
        surface the reason). Unconfirming, and editing a row that is already confirmed
        (e.g. its percentage), are always allowed regardless of confidence.
        """
        attrs = super().validate(attrs)
        was_confirmed = bool(getattr(self.instance, "is_confirmed", False))
        will_be_confirmed = attrs.get("is_confirmed", was_confirmed)
        if will_be_confirmed and not was_confirmed:
            project = attrs.get("project") or getattr(self.instance, "project", None)
            if project is not None and project.confidence != FULL_CONFIDENCE_PERCENTAGE:
                raise serializers.ValidationError(
                    {
                        "is_confirmed": (
                            "Assignment can only be confirmed when the project confidence (確度) is 100%. "
                            f"Project '{project.name}' is at {project.confidence}%."
                        )
                    }
                )
        return attrs


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
