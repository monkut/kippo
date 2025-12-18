from django.contrib import admin
from django.http import request as DjangoRequest  # noqa: N812

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

    def has_view_permission(self, request: DjangoRequest, obj: ProjectProblemDefinition | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"


@admin.register(ProjectAssumption)
class ProjectAssumptionAdmin(admin.ModelAdmin):
    list_display = ("display_id", "project", "category", "is_internal", "title", "created_datetime")
    list_filter = ("project", "category", "is_internal")
    search_fields = ("title", "details", "project__name")

    def has_view_permission(self, request: DjangoRequest, obj: ProjectAssumption | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"


@admin.register(ProjectBusinessRequirementCategory)
class ProjectBusinessRequirementCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "created_datetime")
    list_filter = ("project",)
    search_fields = ("name", "project__name")

    def has_view_permission(self, request: DjangoRequest, obj: ProjectBusinessRequirementCategory | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"


@admin.register(ProjectBusinessRequirement)
class ProjectBusinessRequirementAdmin(admin.ModelAdmin):
    list_display = ("display_id", "project", "category", "title", "created_datetime")
    list_filter = ("project", "category")
    search_fields = ("title", "details", "project__name")
    filter_horizontal = ("problems",)

    def has_view_permission(self, request: DjangoRequest, obj: ProjectBusinessRequirement | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"


@admin.register(ProjectBusinessRequirementComment)
class ProjectBusinessRequirementCommentAdmin(admin.ModelAdmin):
    list_display = ("requirement", "created_by", "is_resolved", "created_datetime")
    list_filter = ("requirement__project", "is_resolved")
    search_fields = ("comment", "requirement__title")

    def has_view_permission(self, request: DjangoRequest, obj: ProjectBusinessRequirementComment | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"


@admin.register(ProjectTechnicalRequirementCategory)
class ProjectTechnicalRequirementCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "created_datetime")
    list_filter = ("project",)
    search_fields = ("name", "project__name")

    def has_view_permission(self, request: DjangoRequest, obj: ProjectTechnicalRequirementCategory | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"


@admin.register(ProjectTechnicalRequirement)
class ProjectTechnicalRequirementAdmin(admin.ModelAdmin):
    list_display = ("display_id", "project", "category", "title", "created_datetime")
    list_filter = ("project", "category")
    search_fields = ("title", "details", "project__name")
    filter_horizontal = ("business_requirements",)

    def has_view_permission(self, request: DjangoRequest, obj: ProjectTechnicalRequirement | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"


@admin.register(ProjectTechnicalRequirementComment)
class ProjectTechnicalRequirementCommentAdmin(admin.ModelAdmin):
    list_display = ("requirement", "created_by", "created_datetime")
    list_filter = ("requirement__project",)
    search_fields = ("comment", "requirement__title")

    def has_view_permission(self, request: DjangoRequest, obj: ProjectTechnicalRequirementComment | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"


@admin.register(ProjectBusinessRequirementEstimate)
class ProjectBusinessRequirementEstimateAdmin(admin.ModelAdmin):
    list_display = ("requirement", "days", "confidence", "created_by", "created_datetime")
    list_filter = ("requirement__project",)
    search_fields = ("requirement__title",)

    def has_view_permission(self, request: DjangoRequest, obj: ProjectBusinessRequirementEstimate | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"


@admin.register(ProjectTechnicalRequirementGithubIssue)
class ProjectTechnicalRequirementGithubIssueAdmin(admin.ModelAdmin):
    list_display = ("technical_requirement", "url", "created_datetime")
    list_filter = ("technical_requirement__project",)
    search_fields = ("url", "technical_requirement__title")

    def has_view_permission(self, request: DjangoRequest, obj: ProjectTechnicalRequirementGithubIssue | None = None) -> bool:
        # only for superusers, cannot return False, the module
        # wouldn't be visible in admin
        return request.user.is_superuser and request.method != "POST"
