"""Monthly infrastructure cost per project."""

from commons.models import TimestampedModel
from django.db import models
from django.utils.translation import gettext_lazy as _

from ..definitions import (
    ValidCurrencies,
    ValidServices,
)
from .project import KippoProject


class ProjectMonthlyCost(TimestampedModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE)
    month = models.DateField(null=True, blank=True, help_text=_("COST month (defaults to project start_date month)"))
    service = models.CharField(max_length=50, choices=ValidServices.choices())
    cost = models.FloatField()
    currency = models.CharField(choices=ValidCurrencies.choices(), default=ValidCurrencies.USD.value, max_length=50)
    itemized_cost = models.JSONField(null=True)  # {"item_name": {VALUE}, ...}

    def __str__(self) -> str:
        display_month = self.month.strftime("%Y-%m")
        return f"{self.project.display_label}({self.project.id}) [{display_month}]"
