"""Project milestones mirrored to GitHub repository milestones."""

import datetime
import logging
import uuid
from collections import Counter

from accounts.models import KippoUser, OrganizationMembership
from commons.models import UserCreatedBaseModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max, QuerySet
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ghorgs.managers import GithubOrganizationManager
from tasks.models import KippoTaskStatus

from .definitions import GITHUB_MANAGER_USERNAME, UNPROCESSABLE_ENTITY_422
from .project import KippoProject

logger = logging.getLogger(__name__)


class KippoMilestone(UserCreatedBaseModel):
    """Provides milestone definition and mapping to a Github Repository Milestone"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, verbose_name=_("Kippo Project"), editable=False)
    title = models.CharField(max_length=256, verbose_name=_("Title"))
    number = models.PositiveSmallIntegerField(editable=False, help_text=_("Internal Per Project Management Number"))
    allocated_staff_days = models.PositiveSmallIntegerField(null=True, blank=True, help_text=_("Budget Allocated Staff Days"))
    is_completed = models.BooleanField(_("Is Completed"), default=False)
    start_date = models.DateField(_("Start Date"), null=True, blank=True, default=None, help_text=_("Milestone Start Date"))
    target_date = models.DateField(_("Target Date"), null=True, blank=True, default=None, help_text=_("Milestone Target Completion Date"))
    actual_date = models.DateField(_("Actual Date"), null=True, blank=True, default=None, help_text=_("Milestone Actual Completion Date"))
    description = models.TextField(_("Description"), blank=True, default="", help_text=_("Describe the purpose of the milestone"))

    class Meta:
        verbose_name = _("マイルストーン")
        verbose_name_plural = verbose_name
        unique_together = ("project", "start_date", "target_date")

    @property
    def github_state(self) -> str:
        """
        Mapping of KippoMilestone is_completed to github milestone state value
        Github valid states are: open, closed, or all
        https://developer.github.com/v3/issues/milestones/

        :return: ('open'|'closed')
        """
        return "open" if not self.is_completed else "closed"

    def clean(self):
        if self.actual_date and (self.actual_date > timezone.now().date()):
            raise ValidationError(_("Given date is in the future"))

        # check start/target date
        if (self.start_date and self.target_date) and self.target_date < self.start_date:
            raise ValidationError(f"start_date({self.start_date}) > target_date({self.target_date})")

    def get_absolute_url(self):
        return f"{settings.URL_PREFIX}/admin/projects/kippomilestone/{self.id}/change/"

    def get_url(self):
        """Url for non-admin page"""
        return f"{settings.URL_PREFIX}/projects/milestones/{self.id}/"

    @property
    def is_delayed(self):
        return not self.is_completed and not self.actual_date and self.target_date and self.target_date < timezone.now().date()

    @property
    def estimated_completion_date(self) -> datetime.date | None:
        from tasks.functions import get_projects_load, get_ttlhash

        # project_developer_load
        # {'PROJECT_ID':  # multiple
        #     {
        #         'GITHUB_LOGIN': [
        #             KippoTask(),  # with 'qlu_task' attribute with scheduled QluTask object
        #             KippoTask()  # with 'qlu_task' attribute with scheduled QluTask object
        #                 ...
        #         ]
        #     },
        # }
        project_developer_load, _, _ = get_projects_load(organization=self.project.organization, ttl_hash=get_ttlhash(seconds=60))

        # retrieve the number of estimated days assigned to this milestone
        max_effort_date = None
        milestone_scheduled_effort_dates = []
        for project_id, project_task_data in project_developer_load.items():
            if project_id != self.project.id:
                logger.debug(f"project_id({project_id}) != milestone.project.id({self.project.id})")
                continue
            for user_assigned_tasks in project_task_data.values():
                for task in user_assigned_tasks:
                    if task.milestone == self:
                        # get assigned dates
                        for date in task.qlu_task.get_scheduled_dates():
                            logger.debug(f"scheduled task({task.title}) date: {date}")
                            milestone_scheduled_effort_dates.append(date)
        if milestone_scheduled_effort_dates:
            max_effort_date = max(milestone_scheduled_effort_dates)
        return max_effort_date

    def get_assignee_workdays(self, start_date: datetime.date | None = None) -> Counter:
        if not start_date:
            current_datetime = timezone.now()
            # TODO: review -- this was set to day = 1... not sure if there was a specific reason for that
            start_datetime = datetime.datetime(current_datetime.year, current_datetime.month, current_datetime.day, tzinfo=datetime.UTC)
            start_date = start_datetime.date()
            start_date = max(start_date, self.start_date)

        # get organization memberships
        organization_memberships = list(
            OrganizationMembership.objects.filter(organization=self.project.organization, user__github_login__isnull=False, is_developer=True)
            .exclude(user__github_login__startswith=settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX)
            .order_by("user__github_login")
        )
        member_personal_holiday_dates = {m.user.github_login: tuple(m.user.personal_holiday_dates()) for m in organization_memberships}
        member_public_holiday_dates = {m.user.github_login: tuple(m.user.public_holiday_dates()) for m in organization_memberships}

        # initialize counter for organization_memberships to zero (0)
        assignee_available_workdays = Counter({m.user: 0 for m in organization_memberships})
        current_date = start_date
        while current_date <= self.target_date:
            for membership in organization_memberships:
                if (
                    current_date not in member_personal_holiday_dates[membership.user.github_login]
                    and current_date not in member_public_holiday_dates[membership.user.github_login]
                    and current_date.weekday() in membership.committed_weekdays
                ):
                    assignee_available_workdays[membership.user] += 1
            current_date += datetime.timedelta(days=1)
        return assignee_available_workdays

    @property
    def assignee_available_workdays(self) -> str:
        assignee_available_workdays = self.get_assignee_workdays()
        return ", ".join(f"{assignee}={workdays}" for assignee, workdays in assignee_available_workdays.items())

    def available_work_days(self, start_date: datetime.date | None = None) -> int:
        """Calculated the work days available considering the FULL OrganizationMembership available assignments"""
        assignee_available_workdays = self.get_assignee_workdays(start_date)
        total_available_workdays = sum(assignee_available_workdays.values())
        return total_available_workdays

    @property
    def estimated_work_days(self) -> int:
        """Return the effort days assigned to tasks in the given milestone"""
        # retrieve the number of estimated days assigned to this milestone
        assignee_estimated_workdays = self.get_assignee_estimated_workdays()
        total_assignee_estimated_workdays = sum(assignee_estimated_workdays.values())
        return total_assignee_estimated_workdays

    def get_assignee_task_counts(self) -> Counter:
        assignee_task_counts = Counter()
        active_task_states = self.project.columnset.get_active_column_names()
        results = KippoTaskStatus.objects.filter(task__in=self.tasks).order_by("task", "-effort_date").distinct("task")
        for r in results:
            if r.state in active_task_states:
                assignee_task_counts[r.task.assignee] += 1
        return assignee_task_counts

    def get_assignee_estimated_workdays(self) -> Counter:
        assignee_estimated_workdays = Counter()
        active_task_states = self.project.columnset.get_active_column_names()
        results = KippoTaskStatus.objects.filter(task__in=self.tasks).order_by("task", "-effort_date").distinct("task")
        for r in results:
            if r.state in active_task_states:
                logger.info(f"adding estimate {r} estimate_days={r.estimate_days}")
                estimate_days = settings.FALLBACK_ESTIMATE_DAYS
                if r.estimate_days:
                    estimate_days = r.estimate_days
                else:
                    logger.warning(f"{r} estimate_days is None, using settings.FALLBACK_ESTIMATE_DAYS={settings.FALLBACK_ESTIMATE_DAYS}")
                assignee = r.task.assignee
                assignee_estimated_workdays[assignee] += estimate_days
        return assignee_estimated_workdays

    @property
    def tasks(self) -> QuerySet:
        return self.kippotask_milestone.order_by("assignee")  # reverse relation to KippoTask

    @property
    def active_tasks(self) -> QuerySet:
        active_task_states = self.project.columnset.get_active_column_names()
        task_ids = (
            KippoTaskStatus.objects.filter(task__in=self.tasks).order_by("task", "-effort_date").distinct("task").values_list("state", "task__id")
        )
        # filter out non-active tasks
        active_task_ids = [task_id for task_state, task_id in task_ids if task_state in active_task_states]
        results = self.kippotask_milestone.filter(pk__in=active_task_ids).order_by("assignee")
        return results

    def update_github_milestones(self, user: KippoUser | None = None, close: bool = False) -> list[tuple[bool, object]]:
        """
        Create or Update related github milestones belonging to github repositories attached to the related project.
        :return:
            .. code:: python
                [
                    (CREATED, GithubMilestone Object),
                ]
        """
        from octocat.models import GITHUB_MILESTONE_CLOSE_STATE, GithubMilestone

        github_milestones = []
        if not user:
            logger.warning(f"user object not given, using: {GITHUB_MANAGER_USERNAME}")
            user = KippoUser.objects.get(username=GITHUB_MANAGER_USERNAME)

        # collect existing
        existing_github_milestones_by_repo_html_url = {}
        existing_github_repositories_by_html_url = {}
        for github_repository in self.project.related_github_repositories():
            url = github_repository.html_url
            url = url.removesuffix("/")
            existing_github_repositories_by_html_url[url] = github_repository
            for github_milestone in GithubMilestone.objects.filter(repository=github_repository, milestone=self):
                existing_github_milestones_by_repo_html_url[url] = github_milestone

        github_organization_name = self.project.organization.github_organization_name
        token = self.project.organization.githubaccesstoken.token
        manager = GithubOrganizationManager(organization=github_organization_name, token=token)

        # identify related github project and get related repository urls
        related_repository_html_urls = list(existing_github_repositories_by_html_url.keys())
        if not related_repository_html_urls:
            logger.warning(f"Related Repository URLS not found for KippoProject: {self.project.name}")
        else:
            for repository in manager.repositories():
                if repository.html_url in related_repository_html_urls:
                    logger.info(f"Updating {repository.name} Milestones...")
                    created = False
                    github_state = self.github_state
                    if close:
                        github_state = GITHUB_MILESTONE_CLOSE_STATE
                    if repository.html_url in existing_github_milestones_by_repo_html_url:
                        github_milestone = existing_github_milestones_by_repo_html_url[repository.html_url]
                        logger.debug(f"Updating Existing Github Milestone({self.title}) for Repository({repository.name}) ...")
                        repository.update_milestone(
                            title=self.title,
                            description=self.description,
                            due_on=self.target_date,
                            state=github_state,
                            number=github_milestone.number,
                        )
                        # mark as updated
                        github_milestone.updated_by = user
                        github_milestone.save()
                    else:
                        logger.debug(f"Creating NEW Github Milestone for Repository({repository.name}) ...")
                        response = repository.create_milestone(
                            title=self.title, description=self.description, due_on=self.target_date, state=github_state
                        )

                        # get number and create GithubMilestone entry
                        # milestone_content defined at:
                        # https://developer.github.com/v3/issues/milestones/#create-a-milestone
                        status_code, milestone_content = response
                        if status_code == UNPROCESSABLE_ENTITY_422:
                            # indicates milestone already exists on github
                            logger.warning(
                                f"422 response from github, milestone may already exist for repository({repository.name}): {milestone_content}"
                            )
                            continue

                        number = milestone_content["number"]
                        api_url = milestone_content["url"]
                        html_url = milestone_content["html_url"]
                        github_repository = existing_github_repositories_by_html_url[repository.html_url]
                        github_milestone = GithubMilestone(
                            milestone=self,
                            created_by=user,
                            updated_by=user,
                            number=number,
                            repository=github_repository,
                            api_url=api_url,
                            html_url=html_url,
                        )
                        github_milestone.save()
                        created = True
                    action = "create" if created else "update"
                    logger.info(f"+ {action} Github Milestone: ({repository.name}) {self.title}")
                    github_milestones.append((created, github_milestone))
        return github_milestones

    def save(self, *args, **kwargs):
        if self._state.adding:  # created
            # assign project number
            existing_milestone_count = KippoMilestone.objects.filter(project=self.project).count()
            if existing_milestone_count > 1:
                # Milestones may be deleted, make sure to use a number that is not in use
                # use existing max number + 1
                max_project_number = KippoMilestone.objects.filter(project=self.project).aggregate(Max("number"))["number__max"]
                self.number = max_project_number + 1
            else:
                self.number = 0

        # auto-update is_completed field if actual_date is entered
        if self.actual_date and self.actual_date < timezone.now().date():
            self.is_completed = True

        # auto-set actual date if complete is set and actual not defined
        if self.is_completed and not self.actual_date:
            self.actual_date = timezone.now().date()
        elif not self.is_completed and self.actual_date:
            # clear set date if is_completed returns to False
            self.actual_date = None

        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.title})"


@receiver(pre_delete, sender=KippoMilestone)
def cleanup_github_milestones(sender: type[KippoMilestone], instance: KippoMilestone, **kwargs):  # noqa: ARG001
    """Close related Github milestones when  KippoMilestone is deleted."""
    from octocat.models import GithubMilestone

    try:
        related_github_milestones = GithubMilestone.objects.filter(milestone=instance).exists()
        if related_github_milestones:
            instance.update_github_milestones(close=True)
    except GithubMilestone.DoesNotExist:
        logger.info("no related GithubMilestone, will not attempt to close on github")
