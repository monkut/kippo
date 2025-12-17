from typing import TYPE_CHECKING

from commons.models import TimestampedModel, UserCreatedBaseModel
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Max
from django.utils.translation import gettext_lazy as _

from .definitions import AssumptionCategories

if TYPE_CHECKING:
    import uuid


def generate_display_id_number(model_class: type[models.Model], project_id: "uuid.UUID") -> int:
    """Generate the next display_id_number for a model within a project."""
    max_result = model_class.objects.filter(project_id=project_id).aggregate(max_num=Max("display_id_number"))
    return (max_result["max_num"] or 0) + 1


class ProjectProblemDefinition(TimestampedModel):
    """Problem definitions that the project aims to solve."""

    DISPLAY_ID_PREFIX = "P"

    display_id_number = models.PositiveIntegerField(editable=False, default=0)
    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    details = models.TextField(null=False, blank=True, default="")

    class Meta:
        verbose_name = _("Problem Definition")
        verbose_name_plural = _("Problem Definitions")

    @property
    def display_id(self) -> str:
        return f"{self.DISPLAY_ID_PREFIX}{self.display_id_number:02}"

    def save(self, *args, **kwargs) -> None:
        if not self.display_id_number:
            self.display_id_number = generate_display_id_number(ProjectProblemDefinition, self.project_id)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.display_id}: {self.title}"


class ProjectAssumption(TimestampedModel):
    """(前提条件と制約事項) Assumptions & Constraints for a project."""

    DISPLAY_ID_PREFIX = "A"

    display_id_number = models.PositiveIntegerField(editable=False, default=0)
    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE)
    category = models.CharField(choices=AssumptionCategories.choices(), default=AssumptionCategories.ASSUMPTION.value)
    is_internal = models.BooleanField(default=False, help_text=_("社内のみの場合設定"))
    title = models.CharField(max_length=100)
    details = models.TextField(null=False, blank=True, default="")

    class Meta:
        verbose_name = _("Assumption")
        verbose_name_plural = _("Assumptions")

    @property
    def display_id(self) -> str:
        return f"{self.DISPLAY_ID_PREFIX}{self.display_id_number:02}"

    def save(self, *args, **kwargs) -> None:
        if not self.display_id_number:
            self.display_id_number = generate_display_id_number(ProjectAssumption, self.project_id)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.display_id}: {self.title}"


class ProjectBusinessRequirementCategory(TimestampedModel):
    """Categories for organizing business requirements."""

    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name = _("Business Requirement Category")
        verbose_name_plural = _("Business Requirement Categories")
        unique_together = ("project", "name")

    def __str__(self) -> str:
        return f"{self.project.name}: {self.name}"


class ProjectBusinessRequirement(TimestampedModel):
    """Business requirements for a project."""

    DISPLAY_ID_PREFIX = "B"

    problems = models.ManyToManyField(ProjectProblemDefinition, blank=True)
    display_id_number = models.PositiveIntegerField(editable=False, default=0)
    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE)
    category = models.ForeignKey(ProjectBusinessRequirementCategory, on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    details = models.TextField(null=False, blank=True, default="")

    class Meta:
        verbose_name = _("Business Requirement")
        verbose_name_plural = _("Business Requirements")

    @property
    def display_id(self) -> str:
        return f"{self.DISPLAY_ID_PREFIX}{self.display_id_number:02}"

    def save(self, *args, **kwargs) -> None:
        if not self.display_id_number:
            self.display_id_number = generate_display_id_number(ProjectBusinessRequirement, self.project_id)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.display_id}: {self.title}"


class ProjectBusinessRequirementComment(UserCreatedBaseModel):
    """Comments on business requirements with nested reply support."""

    requirement = models.ForeignKey(ProjectBusinessRequirement, on_delete=models.CASCADE)
    parent_comment = models.ForeignKey("self", null=True, blank=True, default=None, on_delete=models.CASCADE)
    comment = models.TextField()
    is_resolved = models.BooleanField(default=False, help_text=_("is_resolved is only relevant for top-level comments"))

    class Meta:
        verbose_name = _("Business Requirement Comment")
        verbose_name_plural = _("Business Requirement Comments")

    def __str__(self) -> str:
        return f"Comment on {self.requirement.display_id}"


class ProjectTechnicalRequirementCategory(TimestampedModel):
    """Categories for organizing technical requirements."""

    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name = _("Technical Requirement Category")
        verbose_name_plural = _("Technical Requirement Categories")
        unique_together = ("project", "name")

    def __str__(self) -> str:
        return f"{self.project.name}: {self.name}"


class ProjectTechnicalRequirement(TimestampedModel):
    """（開発要件）Technical requirements linked to business requirements."""

    DISPLAY_ID_PREFIX = "T"

    business_requirements = models.ManyToManyField(ProjectBusinessRequirement, blank=True)
    display_id_number = models.PositiveIntegerField(editable=False, default=0)
    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE)
    category = models.ForeignKey(ProjectTechnicalRequirementCategory, on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    details = models.TextField(null=False, blank=True, default="")

    class Meta:
        verbose_name = _("Technical Requirement")
        verbose_name_plural = _("Technical Requirements")

    @property
    def display_id(self) -> str:
        return f"{self.DISPLAY_ID_PREFIX}{self.display_id_number:02}"

    def save(self, *args, **kwargs) -> None:
        if not self.display_id_number:
            self.display_id_number = generate_display_id_number(ProjectTechnicalRequirement, self.project_id)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.display_id}: {self.title}"


class ProjectTechnicalRequirementComment(UserCreatedBaseModel):
    """Comments on technical requirements with nested reply support."""

    requirement = models.ForeignKey(ProjectTechnicalRequirement, on_delete=models.CASCADE)
    parent_comment = models.ForeignKey("self", null=True, blank=True, default=None, on_delete=models.CASCADE)
    comment = models.TextField()

    class Meta:
        verbose_name = _("Technical Requirement Comment")
        verbose_name_plural = _("Technical Requirement Comments")

    def __str__(self) -> str:
        return f"Comment on {self.requirement.display_id}"


class ProjectBusinessRequirementEstimate(UserCreatedBaseModel):
    """Estimate for a technical requirement (days and confidence)."""

    requirement = models.OneToOneField(ProjectTechnicalRequirement, on_delete=models.CASCADE)
    days = models.FloatField(validators=[MinValueValidator(0.5), MaxValueValidator(30.0)])
    confidence = models.FloatField(validators=[MinValueValidator(0.1), MaxValueValidator(1.0)])

    class Meta:
        verbose_name = _("Requirement Estimate")
        verbose_name_plural = _("Requirement Estimates")

    @property
    def confidence_adjusted_days(self) -> float | None:
        if not self.days:
            return None
        return self.days * (1 + (1 - self.confidence))

    def __str__(self) -> str:
        return f"Estimate for {self.requirement.display_id}: {self.days} days"


class ProjectTechnicalRequirementGithubIssue(TimestampedModel):
    """Links technical requirements to GitHub issues."""

    technical_requirement = models.ForeignKey(ProjectTechnicalRequirement, on_delete=models.CASCADE)
    url = models.URLField()

    class Meta:
        verbose_name = _("Technical Requirement GitHub Issue")
        verbose_name_plural = _("Technical Requirement GitHub Issues")

    def __str__(self) -> str:
        return f"GitHub Issue for {self.technical_requirement.display_id}"
