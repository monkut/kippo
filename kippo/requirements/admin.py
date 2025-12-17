from django.contrib import admin

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


@admin.register(ProjectProblemDefinition)
class ProjectProblemDefinitionAdmin(admin.ModelAdmin):
    list_display = ("display_id", "project", "title", "created_datetime")
    list_filter = ("project",)
    search_fields = ("title", "details", "project__name")


@admin.register(ProjectAssumption)
class ProjectAssumptionAdmin(admin.ModelAdmin):
    list_display = ("display_id", "project", "category", "is_internal", "title", "created_datetime")
    list_filter = ("project", "category", "is_internal")
    search_fields = ("title", "details", "project__name")


@admin.register(ProjectBusinessRequirementCategory)
class ProjectBusinessRequirementCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "created_datetime")
    list_filter = ("project",)
    search_fields = ("name", "project__name")


@admin.register(ProjectBusinessRequirement)
class ProjectBusinessRequirementAdmin(admin.ModelAdmin):
    list_display = ("display_id", "project", "category", "title", "created_datetime")
    list_filter = ("project", "category")
    search_fields = ("title", "details", "project__name")
    filter_horizontal = ("problems",)


@admin.register(ProjectBusinessRequirementComment)
class ProjectBusinessRequirementCommentAdmin(admin.ModelAdmin):
    list_display = ("requirement", "created_by", "is_resolved", "created_datetime")
    list_filter = ("requirement__project", "is_resolved")
    search_fields = ("comment", "requirement__title")


@admin.register(ProjectTechnicalRequirementCategory)
class ProjectTechnicalRequirementCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "created_datetime")
    list_filter = ("project",)
    search_fields = ("name", "project__name")


@admin.register(ProjectTechnicalRequirement)
class ProjectTechnicalRequirementAdmin(admin.ModelAdmin):
    list_display = ("display_id", "project", "category", "title", "created_datetime")
    list_filter = ("project", "category")
    search_fields = ("title", "details", "project__name")
    filter_horizontal = ("business_requirements",)


@admin.register(ProjectTechnicalRequirementComment)
class ProjectTechnicalRequirementCommentAdmin(admin.ModelAdmin):
    list_display = ("requirement", "created_by", "created_datetime")
    list_filter = ("requirement__project",)
    search_fields = ("comment", "requirement__title")


@admin.register(ProjectBusinessRequirementEstimate)
class ProjectBusinessRequirementEstimateAdmin(admin.ModelAdmin):
    list_display = ("requirement", "days", "confidence", "created_by", "created_datetime")
    list_filter = ("requirement__project",)
    search_fields = ("requirement__title",)


@admin.register(ProjectTechnicalRequirementGithubIssue)
class ProjectTechnicalRequirementGithubIssueAdmin(admin.ModelAdmin):
    list_display = ("technical_requirement", "url", "created_datetime")
    list_filter = ("technical_requirement__project",)
    search_fields = ("url", "technical_requirement__title")
