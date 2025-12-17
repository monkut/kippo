import datetime

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import (
    ProjectAssumption,
    ProjectBusinessRequirement,
    ProjectBusinessRequirementCategory,
    ProjectBusinessRequirementComment,
    ProjectBusinessRequirementEstimate,
    ProjectProblemDefinition,
    ProjectTechnicalRequirement,
    ProjectTechnicalRequirementCategory,
    ProjectTechnicalRequirementComment,
    ProjectTechnicalRequirementGithubIssue,
)


class ProjectProblemDefinitionSerializer(serializers.ModelSerializer):
    display_id = serializers.CharField(read_only=True)

    class Meta:
        model = ProjectProblemDefinition
        fields = [
            "id",
            "display_id",
            "project",
            "title",
            "details",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["created_datetime", "updated_datetime"]


class ProjectAssumptionSerializer(serializers.ModelSerializer):
    display_id = serializers.CharField(read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = ProjectAssumption
        fields = [
            "id",
            "display_id",
            "project",
            "category",
            "category_display",
            "is_internal",
            "title",
            "details",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["created_datetime", "updated_datetime"]


class ProjectBusinessRequirementCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectBusinessRequirementCategory
        fields = ["id", "project", "name", "created_datetime", "updated_datetime"]
        read_only_fields = ["created_datetime", "updated_datetime"]


class ProjectTechnicalRequirementCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectTechnicalRequirementCategory
        fields = ["id", "project", "name", "created_datetime", "updated_datetime"]
        read_only_fields = ["created_datetime", "updated_datetime"]


class ProjectBusinessRequirementCommentSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.display_name", read_only=True)
    replies = serializers.SerializerMethodField()

    class Meta:
        model = ProjectBusinessRequirementComment
        fields = [
            "id",
            "requirement",
            "parent_comment",
            "comment",
            "is_resolved",
            "created_by",
            "created_by_name",
            "updated_by",
            "created_datetime",
            "updated_datetime",
            "replies",
        ]
        read_only_fields = [
            "created_by",
            "updated_by",
            "created_datetime",
            "updated_datetime",
        ]

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_replies(self, obj: ProjectBusinessRequirementComment) -> list[dict]:
        replies = ProjectBusinessRequirementComment.objects.filter(parent_comment=obj)
        return ProjectBusinessRequirementCommentSerializer(replies, many=True).data


class ProjectTechnicalRequirementCommentSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.display_name", read_only=True)
    replies = serializers.SerializerMethodField()

    class Meta:
        model = ProjectTechnicalRequirementComment
        fields = [
            "id",
            "requirement",
            "parent_comment",
            "comment",
            "created_by",
            "created_by_name",
            "updated_by",
            "created_datetime",
            "updated_datetime",
            "replies",
        ]
        read_only_fields = [
            "created_by",
            "updated_by",
            "created_datetime",
            "updated_datetime",
        ]

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_replies(self, obj: ProjectTechnicalRequirementComment) -> list[dict]:
        replies = ProjectTechnicalRequirementComment.objects.filter(parent_comment=obj)
        return ProjectTechnicalRequirementCommentSerializer(replies, many=True).data


class ProjectBusinessRequirementEstimateSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.display_name", read_only=True)
    confidence_adjusted_days = serializers.FloatField(read_only=True)

    class Meta:
        model = ProjectBusinessRequirementEstimate
        fields = [
            "id",
            "requirement",
            "days",
            "confidence",
            "confidence_adjusted_days",
            "created_by",
            "created_by_name",
            "updated_by",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "created_by",
            "updated_by",
            "created_datetime",
            "updated_datetime",
        ]

    def validate_confidence(self, value: float) -> float:
        confidence_min = 0.1
        confidence_max = 1.0
        if value < confidence_min or value > confidence_max:
            raise serializers.ValidationError(f"Confidence must be between {confidence_min} and {confidence_max}")
        return value


class ProjectTechnicalRequirementGithubIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectTechnicalRequirementGithubIssue
        fields = [
            "id",
            "technical_requirement",
            "url",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["created_datetime", "updated_datetime"]


class ProjectTechnicalRequirementSerializer(serializers.ModelSerializer):
    display_id = serializers.CharField(read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    estimate = ProjectBusinessRequirementEstimateSerializer(source="projectbusinessrequirementestimate", read_only=True)
    github_issues = ProjectTechnicalRequirementGithubIssueSerializer(source="projecttechnicalrequirementgithubissue_set", many=True, read_only=True)

    class Meta:
        model = ProjectTechnicalRequirement
        fields = [
            "id",
            "display_id",
            "project",
            "business_requirements",
            "category",
            "category_name",
            "title",
            "details",
            "estimate",
            "github_issues",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["created_datetime", "updated_datetime"]


class ProjectTechnicalRequirementDetailSerializer(serializers.ModelSerializer):
    display_id = serializers.CharField(read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    estimate = ProjectBusinessRequirementEstimateSerializer(source="projectbusinessrequirementestimate", read_only=True)
    github_issues = ProjectTechnicalRequirementGithubIssueSerializer(source="projecttechnicalrequirementgithubissue_set", many=True, read_only=True)
    comments = serializers.SerializerMethodField()

    class Meta:
        model = ProjectTechnicalRequirement
        fields = [
            "id",
            "display_id",
            "project",
            "business_requirements",
            "category",
            "category_name",
            "title",
            "details",
            "estimate",
            "github_issues",
            "comments",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["created_datetime", "updated_datetime"]

    @extend_schema_field(ProjectTechnicalRequirementCommentSerializer(many=True))
    def get_comments(self, obj: ProjectTechnicalRequirement) -> list[dict]:
        top_level_comments = obj.projecttechnicalrequirementcomment_set.filter(parent_comment__isnull=True)
        return ProjectTechnicalRequirementCommentSerializer(top_level_comments, many=True).data


class ProjectBusinessRequirementListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing business requirements."""

    display_id = serializers.CharField(read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    problems_data = ProjectProblemDefinitionSerializer(source="problems", many=True, read_only=True)
    technical_requirements_count = serializers.SerializerMethodField()
    total_estimate_days = serializers.SerializerMethodField()

    class Meta:
        model = ProjectBusinessRequirement
        fields = [
            "id",
            "display_id",
            "project",
            "problems",
            "problems_data",
            "category",
            "category_name",
            "title",
            "details",
            "technical_requirements_count",
            "total_estimate_days",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["created_datetime", "updated_datetime"]

    @extend_schema_field(serializers.IntegerField())
    def get_technical_requirements_count(self, obj: ProjectBusinessRequirement) -> int:
        return obj.projecttechnicalrequirement_set.count()

    @extend_schema_field(serializers.FloatField())
    def get_total_estimate_days(self, obj: ProjectBusinessRequirement) -> float:
        estimates = ProjectBusinessRequirementEstimate.objects.filter(requirement__business_requirements=obj)
        return sum(e.days for e in estimates)


class ProjectBusinessRequirementSerializer(serializers.ModelSerializer):
    """Full serializer for creating/updating business requirements."""

    display_id = serializers.CharField(read_only=True)

    class Meta:
        model = ProjectBusinessRequirement
        fields = [
            "id",
            "display_id",
            "project",
            "problems",
            "category",
            "title",
            "details",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["created_datetime", "updated_datetime"]


class TotalEstimateInlineSerializer(serializers.Serializer):
    """Inline serializer for total estimate response in OpenAPI schema."""

    total_days = serializers.FloatField()
    daily_rate = serializers.IntegerField()
    total_cost = serializers.FloatField()


class ProjectBusinessRequirementDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for business requirement detail view."""

    display_id = serializers.CharField(read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    problems_data = ProjectProblemDefinitionSerializer(source="problems", many=True, read_only=True)
    technical_requirements = ProjectTechnicalRequirementSerializer(source="projecttechnicalrequirement_set", many=True, read_only=True)
    comments = serializers.SerializerMethodField()
    total_estimate = serializers.SerializerMethodField()

    class Meta:
        model = ProjectBusinessRequirement
        fields = [
            "id",
            "display_id",
            "project",
            "problems",
            "problems_data",
            "category",
            "category_name",
            "title",
            "details",
            "technical_requirements",
            "comments",
            "total_estimate",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["created_datetime", "updated_datetime"]

    @extend_schema_field(ProjectBusinessRequirementCommentSerializer(many=True))
    def get_comments(self, obj: ProjectBusinessRequirement) -> list[dict]:
        top_level_comments = obj.projectbusinessrequirementcomment_set.filter(parent_comment__isnull=True)
        return ProjectBusinessRequirementCommentSerializer(top_level_comments, many=True).data

    @extend_schema_field(TotalEstimateInlineSerializer)
    def get_total_estimate(self, obj: ProjectBusinessRequirement) -> dict:
        from django.conf import settings
        from projects.definitions import ProjectRoles

        estimates = ProjectBusinessRequirementEstimate.objects.filter(requirement__business_requirements=obj)
        total_days = sum(e.days for e in estimates)
        project = obj.project
        # Get the developer rate (most commonly used for estimates)
        rate = project.assignment_rates.filter(role=ProjectRoles.DEVELOPER.value).first()
        daily_rate = rate.rate_per_day if rate else settings.DEFAULT_PROJECT_DAILY_RATE
        return {
            "total_days": total_days,
            "daily_rate": daily_rate,
            "total_cost": total_days * daily_rate,
        }


class ScheduleEstimationRequestSerializer(serializers.Serializer):
    """Serializer for schedule estimation request."""

    project = serializers.UUIDField(help_text="Project UUID to schedule requirements for")
    developer_count = serializers.IntegerField(min_value=1, max_value=100, help_text="Number of developers available for the work")
    start_date = serializers.DateField(required=False, help_text="Schedule start date (defaults to today)", allow_null=True)

    def validate_start_date(self, value: datetime.date | None) -> datetime.date | None:
        if value and value < timezone.localdate():
            raise serializers.ValidationError("start_date cannot be in the past")
        return value


class ScheduledRequirementSerializer(serializers.Serializer):
    """Serializer for individual scheduled requirement in the response."""

    id = serializers.IntegerField(help_text="Technical requirement database ID")
    display_id = serializers.CharField(help_text="Human-readable display ID (e.g., TR-001)")
    title = serializers.CharField(help_text="Title of the technical requirement")
    category = serializers.CharField(help_text="Category name of the requirement")
    estimate_days = serializers.FloatField(help_text="Original estimate in days")
    confidence = serializers.FloatField(help_text="Confidence level (0.1-1.0)")
    confidence_adjusted_days = serializers.FloatField(help_text="Estimate adjusted by confidence factor")
    assigned_developer = serializers.CharField(help_text="Developer assignment identifier (e.g., Developer 1)")
    priority = serializers.IntegerField(help_text="Scheduling priority (lower = higher priority)")
    scheduled_start_date = serializers.CharField(allow_null=True, help_text="ISO format start date (null if unscheduled)")
    scheduled_end_date = serializers.CharField(allow_null=True, help_text="ISO format end date (null if unscheduled)")
    is_scheduled = serializers.BooleanField(help_text="Whether this requirement was successfully scheduled")


class ErrorResponseSerializer(serializers.Serializer):
    """Serializer for error responses."""

    error = serializers.CharField(help_text="Error message describing what went wrong")


class ScheduleEstimationResponseSerializer(serializers.Serializer):
    """Serializer for schedule estimation response."""

    estimated_completion_date = serializers.CharField(help_text="ISO format date when all work is expected to complete")
    total_estimate_days = serializers.FloatField(help_text="Sum of all requirement estimates in days")
    total_confidence_adjusted_days = serializers.FloatField(help_text="Sum of confidence-adjusted estimates")
    requirements_count = serializers.IntegerField(help_text="Number of requirements scheduled")
    schedule_start_date = serializers.CharField(help_text="ISO format date used as scheduling start")
    developer_count = serializers.IntegerField(help_text="Number of developers used for scheduling")
    scheduled_requirements = ScheduledRequirementSerializer(many=True, help_text="List of scheduled requirements")
