from typing import TYPE_CHECKING

from accounts.models import KippoUser
from commons.fields import CommaSeparatedField
from django.conf import settings
from django.db.models import Sum
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .definitions import (
    FULL_CONFIDENCE_PERCENTAGE,
    SURVEY_EFFORT_THRESHOLD_PERCENTAGE,
    ProjectProgressStatus,
    ProjectRoles,
)
from .models import (
    KippoProject,
    KippoProjectOrganizationCategory,
    KippoProjectUserStatisfactionResult,
    ProjectAssignmentRate,
    ProjectColumnSet,
    ProjectMonthlyAssignment,
    ProjectMonthlyCost,
    ProjectWeeklyEffort,
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
    # category is a FK to KippoProjectOrganizationCategory; expose it as the category key string for
    # API backward-compatibility. Writes resolve against the global default categories (kippo#30 / T08, T20).
    category = serializers.SlugRelatedField(
        slug_field="key",
        queryset=KippoProjectOrganizationCategory.objects.filter(organization__isnull=True),
        required=False,
        help_text="Project category key (e.g. 'ai-development', 'other', 'non-project').",
    )
    # Human-readable category label for the list/detail view (kippo#39 / T14); `category` stays the key.
    category_label = serializers.CharField(source="category.label", read_only=True, allow_null=True)
    # 請求方法 — distinct billing types across the project's contracts (kippo#39 / T14). Read-only;
    # the billing method moved to KippoProjectContract in kippo#31.
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
            "billing_types",
            "monthly_billing_schedule",
            "slack_channel_name",
            "slack_notification_channel_name",
            "project_manager",
            "project_manager_username",
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
            "billing_date",
            "total_revenue",
            "contract_amount",
            "document_folder_url",
            "docbase_tag",
            "problem_definition",
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
            "customer_document_url",  # linked customer's contract-folder URL (kippo#34 / T04)
            "phase_display",  # human-readable status label (kippo#37 / T10)
            "confidence",  # derived from phase (kippo#36 / T09)
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

        # Required-field validation at project registration (kippo#40 / T19). Create-only — edits of
        # existing rows (and existing data) are unaffected. category/phase always carry model defaults,
        # so the enforced gaps are the genuinely-optional fields. 請求方法 (billing method) lives on
        # KippoProjectContract since kippo#31; the API cannot attach a contract at project-create
        # (no nested write), so that requirement is enforced on the admin registration form instead.
        if self.instance is None:
            required_at_registration = ("customer", "project_manager", "start_date", "target_date")
            missing = {field: "This field is required at project registration." for field in required_at_registration if not attrs.get(field)}
            if missing:
                raise serializers.ValidationError(missing)
        return attrs

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
        from requirements.models import ProjectProblemDefinition

        return ProjectProblemDefinition.objects.filter(project=obj).exists()

    @extend_schema_field(ProjectProgressStatusInlineSerializer(allow_null=True))
    def get_projectstatus_display(self, obj: KippoProject) -> dict | None:
        """Get the project progress status display values."""
        project_progress_status: ProjectProgressStatus = obj.get_projectprogressstatus_values()
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

    @extend_schema_field(WeeklyEffortUserInlineSerializer(many=True))
    def get_weekly_effort_users(self, obj: KippoProject) -> list[dict]:
        """Get list of users with their weekly effort percentages for this project."""
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
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "project_name",
            "user_username",
            "user_display_name",
            "created_datetime",
            "updated_datetime",
        ]

    @extend_schema_field(serializers.CharField())
    def get_user_display_name(self, obj: ProjectWeeklyEffort) -> str:
        """Get the user's display name."""
        user = obj.user
        if hasattr(user, "get_display_name"):
            return user.get_display_name()
        return f"{user.first_name} {user.last_name}".strip() or user.username


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
        """Get the user's OrganizationMembership for the project's organization."""
        from accounts.models import OrganizationMembership

        try:
            return OrganizationMembership.objects.get(
                user=obj.user,
                organization=obj.project.organization,
            )
        except OrganizationMembership.DoesNotExist:
            return None

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
