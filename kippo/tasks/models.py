from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from common.models import UserCreatedBaseModel


class KippoTask(UserCreatedBaseModel):
    title = models.CharField(max_length=256,
                             help_text=_('KippoTask Title'))
    category = models.CharField(max_length=256)
    is_closed = models.BooleanField(default=False)
    project = models.ForeignKey('projects.KippoProject',
                                on_delete=models.CASCADE,
                                null=True,
                                blank=True,
                                related_name='kippotask_project')
    milestone = models.ForeignKey('projects.KippoMilestone',
                                  on_delete=models.CASCADE,
                                  null=True,
                                  blank=True,
                                  related_name='kippotask_milestone')
    assignee = models.ForeignKey('accounts.KippoUser',
                                 on_delete=models.CASCADE,
                                 null=True,
                                 blank=True,
                                 help_text=_('Assigned to User'))
    depends_on = models.ForeignKey('self',
                                   on_delete=models.CASCADE,
                                   null=True,
                                   blank=True)
    github_issue_api_url = models.URLField(null=True,
                                           blank=True)
    github_issue_html_url = models.URLField(null=True,
                                            blank=True)
    description = models.TextField(null=True,
                                   blank=True)

    def latest_kippotaskstatus(self, days: int=None):
        return KippoTaskStatus.objects.filter(task=self).latest()

    def effort_days_remaining(self):
        latest_task_status = KippoTaskStatus.objects.filter(task=self).latest()
        return latest_task_status.estimate_days

    def save(self, *args, **kwargs):
        if self.is_closed and not self.closed_datetime:
            self.closed_datetime = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.__class__.__name__}({self.project}: {self.title})'

    class Meta:
        unique_together = (
            'project',
            'title',
            'assignee',
        )


class KippoTaskStatus(UserCreatedBaseModel):
    task = models.ForeignKey(KippoTask,
                             on_delete=models.CASCADE)
    state = models.CharField(max_length=56,
                             db_index=True,
                             null=True,
                             help_text=_('Populated by the Github Organizational Project column the task exists in'))
    state_priority = models.PositiveSmallIntegerField(null=True,
                                                      blank=True,
                                                      help_text=_('The priority of the task within the given state (column) [smaller is better]'))
    effort_date = models.DateField(default=timezone.now,
                                   db_index=True,
                                   help_text=_('Date that effort spent occurred on.'))
    hours_spent = models.FloatField(null=True,
                                    blank=True,
                                    help_text=_('Hours spent on related KippoTask since last update'))
    minimum_estimate_days = models.FloatField(null=True,
                                              blank=True,
                                              help_text=_('Minimum number of days needed to complete the related KippoTask.'))
    estimate_days = models.FloatField(null=True,
                                      blank=True,
                                      help_text=_('Expected number of days needed to complete the related KippoTask.'))
    maximum_estimate_days = models.FloatField(null=True,
                                              blank=True,
                                              help_text=_('Maximum number of days needed to complete the related KippoTask'))
    comment = models.TextField(null=True,
                               blank=True)

    def display_name(self):
        return str(self)

    def __str__(self):
        return f'{self.__class__.__name__}({self.task.title}: {self.effort_date})'

    class Meta:
        unique_together = (
            'task',
            'effort_date',
        )
        ordering = ('-effort_date', )
        get_latest_by = 'effort_date'
