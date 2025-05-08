import logging
import uuid

from accounts.models import KippoOrganization
from commons.models import UserCreatedBaseModel
from django.conf import settings
from django.contrib.postgres import fields
from django.db import models
from django.utils.translation import gettext_lazy as _

from .functions import update_repository_labels

GITHUB_MILESTONE_CLOSE_STATE = "closed"
GITHUB_REPOSITORY_NAME_MAX_LENGTH = 100


logger = logging.getLogger(__name__)


class GithubRepositoryLabelSet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        KippoOrganization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text=_("Organization to which the labelset belongs to."),
    )
    name = models.CharField(max_length=120, help_text=_("Reference Name For LabelSet"))
    labels = models.JSONField(help_text='Labels defined in the format: [{"name": "category:X", "description": "", "color": "AED6F1"},]')
    created_datetime = models.DateTimeField(auto_now_add=True, editable=False)
    updated_datetime = models.DateTimeField(auto_now=True, editable=False)

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.id}) {self.name}"


class GithubRepository(UserCreatedBaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(KippoOrganization, on_delete=models.CASCADE)
    name = models.CharField(max_length=GITHUB_REPOSITORY_NAME_MAX_LENGTH, verbose_name=_("Github Repository Name"))
    label_set = models.ForeignKey(
        GithubRepositoryLabelSet,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        help_text=_("Github Repository LabelSet"),
    )
    api_url = models.URLField(help_text=_("Github Repository API URL"))
    html_url = models.URLField(help_text=_("Github Repository HTML URL"))

    def save(self, *args, **kwargs):
        if self.organization and not self.label_set:
            self.label_set = self.organization.default_labelset
        if self._state.adding is True and settings.OCTOCAT_APPLY_DEFAULT_LABELSET:
            github_organization_name = self.organization.github_organization_name
            githubaccesstoken = self.organization.githubaccesstoken
            label_definitions = tuple(self.label_set.labels)
            delete = settings.OCTOCAT_DELETE_EXISTING_LABELS_ON_UPDATE
            update_repository_labels(
                github_organization_name,
                githubaccesstoken.token,
                repository_name=str(self.name),
                label_definitions=label_definitions,
                delete=delete,
            )
            msg = f"({self.name}) updating labels with: {self.label_set.name}"
            logger.info(msg)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.name}) html_url={self.html_url}"

    class Meta:
        verbose_name_plural = _("github repositories")
        unique_together = (
            "name",
            "api_url",
            "html_url",
        )


class GithubMilestone(UserCreatedBaseModel):
    """
    For managing linkage with Github Repository Milestones
    A single KippoProject (and Github Organizational Project) may link to multiple Github Repositories.
    Therefore multiple GithubMilestone objects may exist for a single KippoMilestone,
    in order to represent a single *logical* milestone across multiple Github Repositories.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    milestone = models.ForeignKey(
        "projects.KippoMilestone",
        verbose_name=_("Kippo Milestone"),
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        help_text=_("Related Kippo Milestone"),
    )
    repository = models.ForeignKey(GithubRepository, null=True, default=None, on_delete=models.CASCADE)
    number = models.PositiveIntegerField(
        _("Github Milestone Number"),
        editable=False,
        help_text=_("Github Milestone Number (needed for update/delete on github)"),
    )
    api_url = models.URLField(
        _("Github Milestone API URL"),
        blank=True,
        default="",
        help_text=_("Github Repository Milestone API URL"),
    )
    html_url = models.URLField(
        _("Github Milestone HTML URL"),
        blank=True,
        default="",
        help_text=_("Github Repository Milestone HTML URL"),
    )

    class Meta:
        unique_together = ("milestone", "repository", "number")


class GithubAccessToken(UserCreatedBaseModel):
    organization = models.OneToOneField("accounts.KippoOrganization", on_delete=models.CASCADE)
    token = models.CharField(
        max_length=40,
        help_text=_("Github Personal Token for accessing Github Projects, Milestones, Repositories and Issues"),
    )

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.organization.name} [{self.organization.github_organization_name}])"


def webhook_events_default():
    return ["project", "project_card"]


class GithubOrganizationalWebhook(UserCreatedBaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.KippoOrganization", on_delete=models.CASCADE)
    hook_id = models.PositiveSmallIntegerField(null=True, blank=True)
    events = fields.ArrayField(
        default=webhook_events_default,
        base_field=models.CharField(max_length=15),
        help_text=_("Github webhook event(s)"),
    )
    url = models.URLField(default=settings.WEBHOOK_URL, help_text=_("The endpoint which github will send webhook events to"))


WEBHOOK_EVENT_STATES = (
    ("unprocessed", "unprocessed"),
    ("processing", "processing"),
    ("error", "error"),
    ("processed", "processed"),
    ("ignore", "ignore"),
)


class GithubWebhookEvent(models.Model):
    organization = models.ForeignKey("accounts.KippoOrganization", on_delete=models.CASCADE, help_text=_("Organization to which event belongs to"))
    created_datetime = models.DateTimeField(auto_now_add=True, editable=False)
    updated_datetime = models.DateTimeField(auto_now=True, editable=False)
    state = models.CharField(max_length=15, default="unprocessed", choices=WEBHOOK_EVENT_STATES)
    event_type = models.CharField(
        max_length=25,
        blank=True,
        default="",
        help_text=_("X-Github-Event value (See: https://developer.github.com/v3/activity/events/types/)"),
    )
    event = models.JSONField(editable=False)

    def __str__(self) -> str:
        return f"GithubWebhookEvent({self.organization.name}:{self.event_type}:{self.created_datetime}:{self.state})"
