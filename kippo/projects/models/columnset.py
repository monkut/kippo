"""GitHub project column sets."""

import logging
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max
from django.utils.translation import gettext_lazy as _

from ..exceptions import ProjectColumnSetError

logger = logging.getLogger(__name__)


def category_prefixes_default():
    return ["category:", "cat:"]


def estimate_prefixes_default():
    return ["estimate:", "est:"]


class ProjectColumnSet(models.Model):  # not using userdefined model in order to make model definitions more portable
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.KippoOrganization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        editable=False,
        help_text=_("The organization that the columnset belongs to(if null all project may use it)"),
    )
    name = models.CharField(max_length=256, verbose_name=_("Project Column Set Name"))
    default_column_name = models.CharField(
        max_length=256,
        default="planning",
        verbose_name=_("Task default column name (Used when project column position is not known)"),
    )
    created_datetime = models.DateTimeField(auto_now_add=True, editable=False)
    updated_datetime = models.DateTimeField(auto_now=True, editable=False)
    label_category_prefixes = models.JSONField(
        null=True,
        blank=True,
        default=category_prefixes_default,
        help_text=_("Github Issue Labels Category Prefixes"),
    )
    label_estimate_prefixes = models.JSONField(
        null=True,
        blank=True,
        default=estimate_prefixes_default,
        help_text=_("Github Issue Labels Estimate Prefixes"),
    )

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.name})"

    def get_column_names(self):
        column_names = [c.name for c in ProjectColumn.objects.filter(columnset=self).order_by("index")]
        if self.default_column_name not in column_names:
            raise ValueError(f"default_column_name({self.default_column_name}) not defined as column: {column_names}")
        return column_names

    def get_active_column_names(self, with_priority: bool = False) -> list[str]:
        if with_priority:
            names = [(priority, c.name) for priority, c in enumerate(ProjectColumn.objects.filter(columnset=self, is_active=True).order_by("-index"))]
        else:
            names = [c.name for c in ProjectColumn.objects.filter(columnset=self, is_active=True).order_by("index")]
        if not names:
            raise ProjectColumnSetError(f"{self} does not have any ACTIVE columns assigned!")
        return names

    def get_done_column_names(self):
        names = [c.name for c in ProjectColumn.objects.filter(columnset=self, is_done=True).order_by("index")]
        if not names:
            raise ProjectColumnSetError(f"{self} does not have any DONE columns assigned!")
        return names


class ProjectColumn(models.Model):
    columnset = models.ForeignKey(ProjectColumnSet, on_delete=models.CASCADE)
    index = models.PositiveSmallIntegerField(
        _("Column Display Index"),
        default=None,
        blank=True,
        unique=True,
        help_text=_("Github Project Column Display Index (0 start)"),
    )
    name = models.CharField(max_length=256, verbose_name=_("Project Column Display Name"))
    github_id = models.PositiveIntegerField(null=True, blank=True, help_text=_("related github column id assigned on creation"))
    is_active = models.BooleanField(default=False, help_text=_("Set to True if tasks in column are considered ACTIVE"))
    is_done = models.BooleanField(default=False, help_text=_("Set to True if tasks in column are considered DONE"))

    class Meta:
        unique_together = (("columnset", "name"), ("columnset", "index"))

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.columnset.name}-{self.name})"

    def save(self, *args, **kwargs):
        # auto-increment if blank (Consider moving to admin)
        if not self.index and ProjectColumn.objects.filter(columnset=self.columnset).exists():
            # get max value of current and increment by 1
            max_index = ProjectColumn.objects.filter(columnset=self.columnset).aggregate(Max("index"))["index__max"]
            self.index = max_index + 1
            logger.info(f"{str(self)} incrementing: {self.index}")
        super().save(*args, **kwargs)

    def clean(self):
        if self.is_active and self.is_done:
            raise ValidationError("(Invalid Configuration) Both is_active and is_done set to True!")
