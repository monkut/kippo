"""GitHub issue collection runs."""

from commons.models import UserCreatedBaseModel
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from .constants import VALID_COLLECTISSUESPROJECTRESULT_STATES


class CollectIssuesAction(UserCreatedBaseModel):
    start_datetime = models.DateTimeField(default=timezone.now)
    end_datetime = models.DateTimeField(null=True, default=None)
    organization = models.ForeignKey("accounts.KippoOrganization", on_delete=models.CASCADE)

    @property
    def status(self):
        total_count = CollectIssuesProjectResult.objects.filter(action=self).count()
        completed_count = CollectIssuesProjectResult.objects.filter(action=self, state="complete").count()
        if total_count:
            percentage = round((completed_count / total_count) * 100, 2)
            result = f"{completed_count}/{total_count} {percentage}%"
        else:
            result = "0/0 0.00%"
        return result

    @property
    def new_task_count(self):
        sum_result = CollectIssuesProjectResult.objects.filter(action=self).aggregate(Sum("new_task_count"))
        result = 0
        if sum_result:
            result = sum_result.get("new_taskstatus_count__sum", 0)
        return result

    @property
    def new_taskstatus_count(self):
        sum_result = CollectIssuesProjectResult.objects.filter(action=self).aggregate(Sum("new_taskstatus_count"))
        result = 0
        if sum_result:
            result = sum_result.get("new_taskstatus_count__sum", 0)
        return result

    @property
    def updated_taskstatus_count(self):
        sum_result = CollectIssuesProjectResult.objects.filter(action=self).aggregate(Sum("updated_taskstatus_count"))
        result = 0
        if sum_result:
            result = sum_result.get("new_taskstatus_count__sum", 0)
        return result

    def save(self, *args, **kwargs):
        total_count = CollectIssuesProjectResult.objects.filter(action=self).count()
        completed_count = CollectIssuesProjectResult.objects.filter(action=self, state="complete").count()
        if total_count and completed_count == total_count:
            self.end_datetime = timezone.now()
        super().save(*args, **kwargs)


class CollectIssuesProjectResult(models.Model):
    action = models.ForeignKey(CollectIssuesAction, on_delete=models.CASCADE)
    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE)
    state = models.CharField(max_length=10, choices=VALID_COLLECTISSUESPROJECTRESULT_STATES, default="processing")
    new_task_count = models.PositiveSmallIntegerField(default=0)
    new_taskstatus_count = models.PositiveSmallIntegerField(default=0)
    updated_taskstatus_count = models.PositiveSmallIntegerField(default=0)
    unhandled_issues = models.JSONField()
