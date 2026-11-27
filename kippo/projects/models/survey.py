"""Employee survey results."""

import datetime

from commons.models import UserCreatedBaseModel
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .project import KippoProject


class KippoProjectUserStatisfactionResult(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE)
    SCORE_CHOICES = ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5))
    fullfillment_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name=_("充実した時間"))
    growth_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name=_("成長"))

    class Meta:
        verbose_name = _("振り返り従業員アンケート")
        verbose_name_plural = verbose_name
        unique_together = ("project", "created_by")

    def __str__(self, *args, **kwargs) -> str:
        return f"{self._meta.verbose_name} {self.project.display_label} {self.created_by.display_name}"


def get_current_month() -> datetime.date:
    return timezone.now().replace(day=1).date()


class KippoProjectUserMonthlyStatisfactionResult(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE)
    date = models.DateField(default=get_current_month)
    SCORE_CHOICES = ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5))
    fullfillment_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name=_("充実した時間"))
    growth_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name=_("成長"))

    class Meta:
        verbose_name = _("（月）従業員アンケート")
        verbose_name_plural = verbose_name
        unique_together = ("created_by", "project", "date")

    def __str__(self, *args, **kwargs) -> str:
        return f"{self._meta.verbose_name} {self.project.display_label} ({self.date.strftime('%Y-%m')}) {self.created_by.display_name}"
