from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Sum
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .definitions import ProjectProgressStatus, ProjectRoles
from .models import KippoProject, ProjectAssignmentRate, ProjectMonthlyAssignment, ProjectMonthlyCost, ProjectWeeklyEffort

if TYPE_CHECKING:
    from accounts.models import OrganizationMembership


class ProjectAssignmentRateInlineSerializer(serializers.Serializer):
    """Inline serializer for assignment rate response in OpenAPI schema."""

    role = serializers.CharField()
    rate_per_day = serializers.IntegerField()
    is_default = serializers.BooleanField()


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
    allocated_effort_hours = serializers.SerializerMethodField()
    assignment_rates = serializers.SerializerMethodField()
    has_requirements = serializers.SerializerMethodField()
    projectstatus_display = serializers.SerializerMethodField()
    latest_comment = serializers.SerializerMethodField()
    weekly_effort_users = serializers.SerializerMethodField()

    class Meta:
        model = KippoProject
        fields = [
            "id",
            "organization",
            "organization_name",
            "name",
            "slug",
            "columnset",
            "phase",
            "confidence",
            "category",
            "slack_channel_name",
            "project_manager",
            "project_manager_username",
            "is_closed",
            "display_as_active",
            "display_in_project_report",
            "github_project_html_url",
            "github_project_api_url",
            "allocated_staff_days",
            "allocated_effort_hours",
            "start_date",
            "target_date",
            "actual_date",
            "document_url",
            "problem_definition",
            "survey_issued",
            "assignment_rates",
            "has_requirements",
            "projectstatus_display",
            "latest_comment",
            "weekly_effort_users",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "slug",
            "organization_name",
            "project_manager_username",
            "allocated_effort_hours",
            "assignment_rates",
            "has_requirements",
            "projectstatus_display",
            "latest_comment",
            "weekly_effort_users",
            "created_datetime",
            "updated_datetime",
        ]

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_allocated_effort_hours(self, obj: KippoProject) -> float | None:
        """Calculate allocated effort in hours from staff days."""
        if obj.allocated_staff_days is not None:
            return obj.allocated_staff_days * settings.DAY_WORKHOURS
        return None

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


class ProjectWeeklyEffortSerializer(serializers.ModelSerializer):
    """Serializer for ProjectWeeklyEffort model.

    The `user` field is optional on create - it will be auto-set to the
    authenticated user by the viewset's perform_create method.
    """

    project_name = serializers.CharField(source="project.name", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    user_display_name = serializers.SerializerMethodField()

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
        extra_kwargs = {
            "user": {"required": False, "allow_null": True},  # Auto-set by viewset.perform_create()
        }

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
