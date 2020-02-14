# from typing import Optional
# from math import ceil
#
# from django.conf import settings
from django.db import models
from django.contrib.postgres.fields import JSONField
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

# from ghorgs.managers import GithubOrganizationManager
# from ghorgs.wrappers import GithubIssue
#
# from accounts.models import KippoUser
from common.models import UserCreatedBaseModel

# from .functions import (
#     get_github_issue_prefixed_labels,
#     get_github_issue_category_label,
#     get_github_issue_estimate_label,
#     build_latest_comment,
#     get_tags_from_prefixedlabels
# )


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
    project_card_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        editable=False,
        help_text=_('CardId when task belongs to a specific Github Project')
    )
    description = models.TextField(null=True,
                                   blank=True)

    @property
    def github_repository_html_url(self):
        """Provide the related octocat.models.GithubRepository object"""
        # self.github_issue_html_url
        # https://github.com/myorg/myrepo/issues/133
        # -->
        #       https://github.com/myorg/myrepo
        github_respository_html_url, *_ = self.github_issue_html_url.rsplit('/', 2)
        return github_respository_html_url

    def latest_kippotaskstatus(self):
        return KippoTaskStatus.objects.filter(task=self).latest()

    def effort_days_remaining(self) -> int:
        latest_task_status = KippoTaskStatus.objects.filter(task=self).latest()
        return latest_task_status.estimate_days

    # def get_or_create_kippotaskstatus(self):
    #     """Create a *NEW* KippoTaskStatus object from the latest GithubIssue state"""
    #     assert self.project
    #     status_effort_date = timezone.now().date()
    #     githubissue = self.get_githubissue()
    #     latest_comment = build_latest_comment(githubissue)
    #     self.github_manager_user = KippoUser.objects.get(username=settings.GITHUB_MANAGER_USERNAME)
    #
    #     org_developer_users = self.project.organization.get_github_developer_kippousers()
    #     org_unassigned_user = self.project.organization.get_unassigned_kippouser()
    #     developer_assignees = [
    #         issue_assignee.login
    #         for issue_assignee in githubissue.assignees
    #         if issue_assignee.login in org_developer_users
    #     ]
    #     if not developer_assignees:
    #         # assign task to special 'unassigned' user if task is not assigned to anyone
    #         developer_assignees = [org_unassigned_user.github_login]
    #
    #     estimate_denominator = len(developer_assignees)
    #
    #     unadjusted_issue_estimate = get_github_issue_estimate_label(githubissue)
    #     adjusted_issue_estimate = None
    #     if unadjusted_issue_estimate:
    #         # adjusting to take into account the number of developer_assignees working on it
    #         # -- divides task load by the number of developer_assignees
    #         adjusted_issue_estimate = ceil(unadjusted_issue_estimate / estimate_denominator)
    #
    #     prefixed_labels = get_github_issue_prefixed_labels(githubissue)
    #     tags = get_tags_from_prefixedlabels(prefixed_labels)
    #
    #     # set task state (used to determine if a task is "active" or not)
    #     # -- When a task is "active" the estimate is included in the resulting schedule projection
    #     task_state = githubissue.project_column if githubissue.project_column else self.project.default_column_name
    #
    #     # create or update KippoTaskStatus with updated estimate
    #     status_values = {
    #         'created_by': self.github_manager_user,
    #         'updated_by': self.github_manager_user,
    #         'state': task_state,
    #         'state_priority': githubissue.column_priority,
    #         'estimate_days': adjusted_issue_estimate,
    #         'effort_date': status_effort_date,
    #         'tags': tags,
    #         'comment': latest_comment
    #     }
    #     status, created = KippoTaskStatus.objects.get_or_create(
    #         task=self,
    #         effort_date=status_effort_date,
    #         defaults=status_values
    #     )
    #     return status, created
    #
    # def get_githubissue(self) -> Optional[GithubIssue]:
    #     githubissue = None
    #     if self.project and self.github_issue_api_url:
    #         manager = GithubOrganizationManager(
    #             organization=self.project.organization.github_organization_name,
    #             token=self.project.organization.githubaccesstoken.token
    #         )
    #         githubissue = manager.get_github_issue(self.github_issue_api_url)
    #     return githubissue

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
        get_latest_by = 'created_datetime'


class KippoTaskStatus(UserCreatedBaseModel):
    task = models.ForeignKey(
        KippoTask,
        on_delete=models.CASCADE
    )
    state = models.CharField(
        max_length=56,
        db_index=True,
        null=True,
        help_text=_('Populated by the Github Organizational Project column the task exists in')
    )
    state_priority = models.PositiveSmallIntegerField(null=True,
                                                      blank=True,
                                                      default=0,
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
    tags = JSONField(
        null=True,
        blank=True,
        help_text=_('Any tags/labels related to the current task status')
    )
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
