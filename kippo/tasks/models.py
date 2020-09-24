import logging
import urllib.parse
from typing import Optional

from common.models import UserCreatedBaseModel
from django.contrib.postgres.fields import JSONField
from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

logger = logging.getLogger(__name__)


class KippoTask(UserCreatedBaseModel):
    title = models.CharField(max_length=256, help_text=_("KippoTask Title"))
    category = models.CharField(max_length=256)
    is_closed = models.BooleanField(default=False)
    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE, null=True, blank=True, related_name="kippotask_project")
    milestone = models.ForeignKey("projects.KippoMilestone", on_delete=models.DO_NOTHING, null=True, blank=True, related_name="kippotask_milestone")
    assignee = models.ForeignKey("accounts.KippoUser", on_delete=models.SET_NULL, null=True, blank=True, help_text=_("Assigned to User"))
    depends_on = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True)
    github_issue_api_url = models.URLField(null=True, blank=True)
    github_issue_html_url = models.URLField(null=True, blank=True)
    project_card_id = models.PositiveIntegerField(
        null=True, blank=True, editable=False, help_text=_("CardId when task belongs to a specific Github Project")
    )
    description = models.TextField(null=True, blank=True)

    @property
    def github_repository_html_url(self):
        """Provide the related octocat.models.GithubRepository object"""
        # self.github_issue_html_url
        # https://github.com/myorg/myrepo/issues/133
        # -->
        #       https://github.com/myorg/myrepo
        github_respository_html_url, *_ = self.github_issue_html_url.rsplit("/", 2)
        return github_respository_html_url

    def latest_kippotaskstatus(self) -> Optional["KippoTaskStatus"]:
        status = None
        try:
            status = KippoTaskStatus.objects.filter(task=self).latest()
        except KippoTaskStatus.DoesNotExist:
            pass
        return status

    def effort_days_remaining(self) -> int:
        latest_task_status = KippoTaskStatus.objects.filter(task=self).latest()
        return latest_task_status.estimate_days

    def save(self, *args, **kwargs):
        from accounts.models import get_climanager_user
        from octocat.models import GithubRepository

        if self.is_closed and not self.closed_datetime:
            self.closed_datetime = timezone.now()

        # check if repository already exists
        if self.github_issue_html_url:
            # issue url structure:
            # https://github.com/{ORGANIZATION}/{REPOSITORY}/issues/1
            parsed_url = urllib.parse.urlparse(self.github_issue_html_url)
            repo_name_index = 2
            repository_name = parsed_url.path.split("/")[repo_name_index]
            repository_api_url = f"https://api.github.com/repos/{self.project.organization.github_organization_name}/{repository_name}"
            repository_html_url = f"https://github.com/{self.project.organization.github_organization_name}/{repository_name}"
            try:
                # using '__startswith' to assure match in cases where an *older* url as added with an ending '/'.
                existing_repository = GithubRepository.objects.get(
                    name=repository_name, api_url__startswith=repository_api_url, html_url__startswith=repository_html_url
                )
                logger.debug(f"respository exists: {existing_repository.name}")
            except GithubRepository.DoesNotExist:
                logger.info(f"Creating *NEW* repository({repository_name})...")
                climanager_user = get_climanager_user()
                new_repository = GithubRepository(
                    organization=self.project.organization,
                    name=repository_name,
                    api_url=repository_api_url,
                    html_url=repository_html_url,
                    label_set=self.project.organization.default_labelset,  # may be Null/None
                    created_by=climanager_user,
                    updated_by=climanager_user,
                )
                new_repository.save()
                logger.info(f"Creating *NEW* repository({repository_name})...SUCCESS!")

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.__class__.__name__}({self.project}: {self.title})"

    class Meta:
        unique_together = ("project", "github_issue_api_url")
        get_latest_by = "created_datetime"


class KippoTaskStatus(UserCreatedBaseModel):
    task = models.ForeignKey(KippoTask, on_delete=models.CASCADE)
    state = models.CharField(
        max_length=56, db_index=True, null=True, help_text=_("Populated by the Github Organizational Project column the task exists in")
    )
    state_priority = models.PositiveSmallIntegerField(
        null=True, blank=True, default=0, help_text=_("The priority of the task within the given state (column) [smaller is better]")
    )
    effort_date = models.DateField(default=timezone.now, db_index=True, help_text=_("Date that effort spent occurred on."))
    hours_spent = models.FloatField(null=True, blank=True, help_text=_("Hours spent on related KippoTask since last update"))
    minimum_estimate_days = models.FloatField(null=True, blank=True, help_text=_("Minimum number of days needed to complete the related KippoTask."))
    estimate_days = models.FloatField(null=True, blank=True, help_text=_("Expected number of days needed to complete the related KippoTask."))
    maximum_estimate_days = models.FloatField(null=True, blank=True, help_text=_("Maximum number of days needed to complete the related KippoTask"))
    tags = JSONField(null=True, blank=True, help_text=_("Any tags/labels related to the current task status"))
    comment = models.TextField(null=True, blank=True)

    def display_name(self):
        return str(self)

    def __str__(self):
        return f"{self.__class__.__name__}({self.task.title}: {self.effort_date})"

    class Meta:
        unique_together = ("task", "effort_date")
        ordering = ("-effort_date",)
        get_latest_by = "effort_date"
