from commons.admin import SuperuserViewOnlyAdminMixin
from django.contrib import admin

from .models import (
    AssumptionEvaluation,
    BusinessRequirementEvaluation,
    ProblemDefinitionEvaluation,
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
    TechnicalRequirementEvaluation,
)


@admin.register(ProjectProblemDefinition)
class ProjectProblemDefinitionAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("display_id", "project", "title", "created_datetime")
    list_filter = ("project",)
    search_fields = ("title", "details", "project__name")


@admin.register(ProjectAssumption)
class ProjectAssumptionAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("display_id", "project", "category", "is_internal", "title", "created_datetime")
    list_filter = ("project", "category", "is_internal")
    search_fields = ("title", "details", "project__name")


@admin.register(ProjectBusinessRequirementCategory)
class ProjectBusinessRequirementCategoryAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "project", "created_datetime")
    list_filter = ("project",)
    search_fields = ("name", "project__name")


@admin.register(ProjectBusinessRequirement)
class ProjectBusinessRequirementAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("display_id", "project", "category", "title", "created_datetime")
    list_filter = ("project", "category")
    search_fields = ("title", "details", "project__name")
    filter_horizontal = ("problems",)


@admin.register(ProjectBusinessRequirementComment)
class ProjectBusinessRequirementCommentAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("requirement", "created_by", "is_resolved", "created_datetime")
    list_filter = ("requirement__project", "is_resolved")
    search_fields = ("comment", "requirement__title")


@admin.register(ProjectTechnicalRequirementCategory)
class ProjectTechnicalRequirementCategoryAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "project", "created_datetime")
    list_filter = ("project",)
    search_fields = ("name", "project__name")


@admin.register(ProjectTechnicalRequirement)
class ProjectTechnicalRequirementAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("display_id", "project", "category", "title", "created_datetime")
    list_filter = ("project", "category")
    search_fields = ("title", "details", "project__name")
    filter_horizontal = ("business_requirements",)


@admin.register(ProjectTechnicalRequirementComment)
class ProjectTechnicalRequirementCommentAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("requirement", "created_by", "created_datetime")
    list_filter = ("requirement__project",)
    search_fields = ("comment", "requirement__title")


@admin.register(ProjectBusinessRequirementEstimate)
class ProjectBusinessRequirementEstimateAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("requirement", "days", "confidence", "created_by", "created_datetime")
    list_filter = ("requirement__project",)
    search_fields = ("requirement__title",)


@admin.register(ProjectTechnicalRequirementGithubIssue)
class ProjectTechnicalRequirementGithubIssueAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("technical_requirement", "url", "created_datetime")
    list_filter = ("technical_requirement__project",)
    search_fields = ("url", "technical_requirement__title")


@admin.register(ProblemDefinitionEvaluation)
class ProblemDefinitionEvaluationAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("requirement", "evaluation_result", "created_by", "created_datetime")
    list_filter = ("evaluation_result",)
    search_fields = ("requirement__title", "feedback")


@admin.register(AssumptionEvaluation)
class AssumptionEvaluationAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("requirement", "evaluation_result", "created_by", "created_datetime")
    list_filter = ("evaluation_result",)
    search_fields = ("requirement__title", "feedback")


@admin.register(BusinessRequirementEvaluation)
class BusinessRequirementEvaluationAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("requirement", "evaluation_result", "created_by", "created_datetime")
    list_filter = ("evaluation_result",)
    search_fields = ("requirement__title", "feedback")


@admin.register(TechnicalRequirementEvaluation)
class TechnicalRequirementEvaluationAdmin(SuperuserViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("requirement", "evaluation_result", "created_by", "created_datetime")
    list_filter = ("evaluation_result",)
    search_fields = ("requirement__title", "feedback")
