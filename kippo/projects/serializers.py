from typing import TYPE_CHECKING

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .definitions import ProjectRoles
from .models import KippoProject, ProjectAssignmentRate, ProjectMonthlyAssignment, ProjectWeeklyEffort

if TYPE_CHECKING:
    from accounts.models import OrganizationMembership


class ProjectAssignmentRateInlineSerializer(serializers.Serializer):
    """Inline serializer for assignment rate response in OpenAPI schema."""

    role = serializers.CharField()
    rate_per_day = serializers.IntegerField()
    is_default = serializers.BooleanField()


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


class ProjectWeeklyEffortSerializer(serializers.ModelSerializer):
    """Serializer for ProjectWeeklyEffort model."""

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
