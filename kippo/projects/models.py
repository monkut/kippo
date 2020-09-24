import datetime
import logging
import uuid
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import reversion
from accounts.models import KippoUser, OrganizationMembership
from common.models import UserCreatedBaseModel
from django.conf import settings
from django.contrib.postgres.fields import ArrayField, JSONField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Max, QuerySet, Sum
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import ugettext_lazy as _
from ghorgs.managers import GithubOrganizationManager
from octocat.models import GITHUB_MILESTONE_CLOSE_STATE, GithubMilestone, GithubRepository
from tasks.models import KippoTaskStatus

from .exceptions import ProjectColumnSetError

logger = logging.getLogger(__name__)

UNASSIGNED_USER_GITHUB_LOGIN_PREFIX = settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX
GITHUB_MANAGER_USERNAME = settings.GITHUB_MANAGER_USERNAME
UNPROCESSABLE_ENTITY_422 = 422


def get_target_date_default() -> datetime.date:
    # TODO: update to take into account configured holidays
    return (timezone.now() + timezone.timedelta(days=settings.DEFAULT_KIPPORPOJECT_TARGET_DATE_DAYS)).date()


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
        max_length=256, default="planning", verbose_name=_("Task default column name (Used when project column position is not known)")
    )
    created_datetime = models.DateTimeField(auto_now_add=True, editable=False)
    updated_datetime = models.DateTimeField(auto_now=True, editable=False)
    label_category_prefixes = ArrayField(
        models.CharField(max_length=10, blank=True),
        null=True,
        blank=True,
        default=category_prefixes_default,
        help_text=_("Github Issue Labels Category Prefixes"),
    )
    label_estimate_prefixes = ArrayField(
        models.CharField(max_length=10, blank=True),
        null=True,
        blank=True,
        default=estimate_prefixes_default,
        help_text=_("Github Issue Labels Estimate Prefixes"),
    )

    def get_column_names(self):
        column_names = [c.name for c in ProjectColumn.objects.filter(columnset=self).order_by("index")]
        if self.default_column_name not in column_names:
            raise ValueError(f"default_column_name({self.default_column_name}) not defined as column: {column_names}")
        return column_names

    def get_active_column_names(self, with_priority=False):
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

    def __str__(self):
        return f"{self.__class__.__name__}({self.name})"


class ProjectColumn(models.Model):
    columnset = models.ForeignKey(ProjectColumnSet, on_delete=models.CASCADE)
    index = models.PositiveSmallIntegerField(
        _("Column Display Index"), default=None, blank=True, unique=True, help_text=_("Github Project Column Display Index (0 start)")
    )
    name = models.CharField(max_length=256, verbose_name=_("Project Column Display Name"))
    github_id = models.PositiveIntegerField(null=True, blank=True, help_text=_("related github column id assigned on creation"))
    is_active = models.BooleanField(default=False, help_text=_("Set to True if tasks in column are considered ACTIVE"))
    is_done = models.BooleanField(default=False, help_text=_("Set to True if tasks in column are considered DONE"))

    def clean(self):
        if self.is_active and self.is_done:
            raise ValidationError("(Invalid Configuration) Both is_active and is_done set to True!")

    def save(self, *args, **kwargs):
        # auto-increment if blank (Consider moving to admin)
        if not self.index and ProjectColumn.objects.filter(columnset=self.columnset).exists():
            # get max value of current and increment by 1
            max_index = ProjectColumn.objects.filter(columnset=self.columnset).aggregate(Max("index"))["index__max"]
            self.index = max_index + 1
            logger.info(f"{str(self)} incrementing: {self.index}")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.__class__.__name__}({self.columnset.name}-{self.name})"

    class Meta:
        unique_together = (("columnset", "name"), ("columnset", "index"))


DEFAULT_PROJECT_PHASE = "lead-evaluation"
VALID_PROJECT_PHASES = (
    ("lead-evaluation", "Lead Evaluation"),
    ("project-proposal", "Project Proposal Preparation"),
    ("project-development", "Project Development"),
)


@reversion.register()
class KippoProject(UserCreatedBaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.KippoOrganization", on_delete=models.CASCADE)
    name = models.CharField(max_length=256, unique=True)
    slug = models.CharField(max_length=300, unique=True, editable=False)
    phase = models.CharField(
        max_length=150, default=DEFAULT_PROJECT_PHASE, choices=VALID_PROJECT_PHASES, help_text=_("State or phase of the project")
    )
    confidence = models.PositiveSmallIntegerField(
        default=80,
        validators=(MaxValueValidator(100), MinValueValidator(0)),
        help_text=_("0-100, Confidence level of the project proceeding to the next phase"),
    )
    category = models.CharField(max_length=256, default=settings.DEFAULT_KIPPOPROJECT_CATEGORY)
    slack_channel_name = models.CharField(max_length=80, null=True, blank=True, default=None, help_text=_("If given, updates are sent periodically"))
    columnset = models.ForeignKey(
        ProjectColumnSet,
        on_delete=models.DO_NOTHING,
        help_text=_("ProjectColumnSet to use if/when a related Github project is created through Kippo"),
    )
    project_manager = models.ForeignKey(
        "accounts.KippoUser", on_delete=models.SET_NULL, null=True, blank=True, help_text=_("Project Manager assigned to the project")
    )
    is_closed = models.BooleanField(_("Project is Closed"), default=False, help_text=_("Manually set when project is complete"))
    display_as_active = models.BooleanField(
        _("Display as Active"), default=True, help_text=_("If True, project will be included in the ActiveKippoProject List")
    )
    github_project_html_url = models.URLField(_("Github Project HTML URL"), null=True, blank=True)
    github_project_api_url = models.URLField(_("Github Project api URL (needed for webhook event linking to project)"), null=True, blank=True)
    allocated_staff_days = models.PositiveIntegerField(null=True, blank=True, help_text=_("Estimated Staff Days needed for Project Completion"))
    start_date = models.DateField(_("Start Date"), null=True, blank=True, help_text=_("Date the Project requires engineering resources"))
    target_date = models.DateField(
        _("Target Finish Date"),
        null=True,
        blank=True,
        default=get_target_date_default,
        help_text=_("Date the Project is planned to be completed by."),
    )
    actual_date = models.DateField(
        _("Actual Completed Date"), null=True, blank=True, help_text=_("The date the project was actually completed on (not the initial target)")
    )
    document_url = models.URLField(
        _("Documentation Location URL"), null=True, blank=True, help_text=_("URL of where documents for the projects are maintained")
    )
    problem_definition = models.TextField(
        _("Project Problem Definition"), null=True, blank=True, help_text=_("Define the problem that the project is set out to solve.")
    )
    survey_issued = models.BooleanField(default=False, help_text=_("Update when survey is issued!"))
    survey_issued_datetime = models.DateTimeField(null=True, editable=False, help_text=_('Updated when "survey_issued" flag is set'))
    column_info = JSONField(
        null=True,
        blank=True,
        editable=False,
        # example content (graphql creation result):
        # [
        # {'id': 'MDEzOlByb2plY3RDb2x1bW42MTE5AZQ1', 'name': 'in-progress', 'resourcePath': '/orgs/myorg/projects/21/columns/6119645'},
        # ]
        help_text=_("If project created through Kippo, this field is populated with column info"),
    )

    def get_columnset_id_to_name_mapping(self):
        if not self.column_info:
            raise ValueError(f"KippoProject.column_info not populated, unable to generate ID to Name Mapping!")
        mapping = {}
        for column_definition in self.column_info:
            name = column_definition["name"]
            if "resourcePath" in column_definition:  # when auto-populated on creation (graphql result)
                column_id = column_definition["resourcePath"].split("/")[-1]
            elif "id" in column_definition:  # when manually updated with github-api result
                column_id = column_definition["id"]
            else:
                raise KeyError(f'expected keys("resourcePath", "id") not in column_definition: {column_definition}')
            mapping[int(column_id)] = name
        return mapping

    def get_columnname_from_id(self, column_id: int) -> Optional[str]:
        mapping = self.get_columnset_id_to_name_mapping()
        return mapping.get(column_id, None)

    def clean(self):
        if self.actual_date and self.actual_date > timezone.now().date():
            raise ValidationError(_("Given date is in the future"))

    def developers(self):
        from tasks.models import KippoTask

        return {
            t.assignee
            for t in KippoTask.filter(project=self, assignee__is_developer=True).exclude(
                assignee__github_login__startswith=UNASSIGNED_USER_GITHUB_LOGIN_PREFIX
            )
        }

    @property
    def default_column_name(self):
        return self.columnset.default_column_name

    def get_admin_url(self):
        return f"{settings.URL_PREFIX}/admin/projects/kippoproject/{self.id}/change"

    def get_absolute_url(self):
        return f"{settings.URL_PREFIX}/projects/?slug={self.slug}"

    def get_column_names(self) -> List[str]:
        """
        Get the column names for use in github project columns
        :return: Column names in expected order
        """
        if not self.columnset:
            raise ValueError(_(f"{self}.columnset not defined!"))
        return self.columnset.get_column_names()

    def get_active_column_names(self):
        if not self.columnset:
            raise ValueError(_(f"{self}.columnset not defined!"))
        return self.columnset.get_active_column_names()

    def get_latest_kippoprojectstatus(self):
        try:
            latest_kippoprojectstatus = KippoProjectStatus.objects.filter(project=self).latest("created_datetime")
        except KippoProjectStatus.DoesNotExist:
            latest_kippoprojectstatus = None
        return latest_kippoprojectstatus

    def get_active_taskstatus(
        self, max_effort_date: Optional[timezone.datetime.date] = None, additional_filters: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[KippoTaskStatus], bool]:
        """Get the latest KippoTaskStatus entries for active tasks for the given Project(s)"""
        has_estimates = False
        valid_column_states = self.get_active_column_names() + ["open"]
        qs = KippoTaskStatus.objects.filter(task__github_issue_api_url__isnull=False, task__project=self)  # filter out non-linked tasks
        if additional_filters:
            logger.debug(f"additional_filters={additional_filters}")
            qs = qs.filter(**additional_filters)

        if max_effort_date:
            qs = qs.filter(effort_date__lte=max_effort_date)
        results = qs.order_by("task__github_issue_api_url", "-effort_date").distinct("task__github_issue_api_url")

        # only include active states
        taskstatus_results = [r for r in list(results) if r.state in valid_column_states]
        if any(status.estimate_days for status in taskstatus_results):
            has_estimates = True
        return taskstatus_results, has_estimates

    def get_latest_taskstatuses(
        self, current_date: Optional[timezone.datetime.date] = None, active_only: bool = False
    ) -> QuerySet:  # KippoTaskStatus
        """Get the latest KippoTaskStatus entries for active tasks for the given Project(s)"""
        if not current_date:
            current_date = timezone.now().date()

        target_kippotaskstatus_ids = (
            KippoTaskStatus.objects.filter(
                task__github_issue_api_url__isnull=False, task__project=self, effort_date__lte=current_date  # filter out non-linked tasks
            )
            .order_by("task__github_issue_api_url", "-effort_date")
            .distinct("task__github_issue_api_url")
            .values_list("pk", flat=True)
        )

        # filter by active columns and get desired values
        valid_column_states = self.get_column_names()
        if active_only:
            valid_column_states = self.get_active_column_names() + ["open"]

        status_entries = KippoTaskStatus.objects.filter(pk__in=target_kippotaskstatus_ids, state__in=valid_column_states)
        return status_entries

    def get_projectsurvey_url(self):
        """
        Generate and return the project survey URL pre-populated with project-id
        """
        url = ""
        if self.organization.google_forms_project_survey_url and self.organization.google_forms_project_survey_projectid_entryid:
            params = {
                "usp": "pp_url",  # not sure what this is (pre-populated url?)
                self.organization.google_forms_project_survey_projectid_entryid: self.id,
            }
            encoded_params = urlencode(params)
            url = f"{self.organization.google_forms_project_survey_url}?{encoded_params}"
        return url

    def active_milestones(self):
        today = timezone.now().date()
        return KippoMilestone.objects.filter(project=self, target_date__gte=today).order_by("-target_date")

    def related_github_repositories(self) -> QuerySet:
        """Returns octocat.GithubRepository objects attached to this project."""
        # get kippotask github_repository_html_url
        from tasks.models import KippoTask

        # get related repositories through the KippoTask(s) attached to the KippoProject
        # Includes both formats:
        # -- {repository_url}
        # -- {repository_url}/
        repository_html_urls = set()
        for issue_html_url in KippoTask.objects.filter(project=self).values_list("github_issue_html_url", flat=True):
            logger.debug(f"issue_html_url={issue_html_url}")
            root_repository_url = issue_html_url.rsplit("/", 2)[0]
            # add root
            repository_html_urls.add(root_repository_url)
            # add with
            repository_html_url = f"{root_repository_url}/"
            repository_html_urls.add(repository_html_url)
        return GithubRepository.objects.filter(html_url__in=tuple(repository_html_urls))

    @property
    def github_project_name(self):
        return self.name

    @property
    def github_project_description(self):
        project_manager_display_name = ""
        if self.project_manager:
            project_manager_display_name = self.project_manager.display_name
        description = (
            f"""project_manager: {project_manager_display_name}<br/>"""
            f"""start_date: {self.start_date}                       <br/>"""
            f"""end_date  : {self.target_date}                      <br/>"""
        )
        return description

    def save(self, *args, **kwargs):
        if self.survey_issued and not self.survey_issued_datetime:
            self.survey_issued_datetime = timezone.now()

        if self.is_closed and not self.closed_datetime:
            self.closed_datetime = timezone.now()

        if self._state.adding:  # created
            # perform initial creation tasks
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.__class__.__name__}({self.name})"


class ActiveKippoProjectManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()

        # update so only 'active"
        qs = qs.filter(is_closed=False, display_as_active=True)
        return qs


class ActiveKippoProject(KippoProject):
    objects = ActiveKippoProjectManager()

    class Meta:
        proxy = True


class KippoProjectStatus(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE)
    comment = models.TextField(help_text=_("Current Status"))

    def __str__(self):
        return f"ProjectStatus({self.project.name} {self.created_datetime})"


class GithubMilestoneAlreadyExists(Exception):
    pass


# class GroupedKippoMilestone(UserCreatedBaseModel):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     title = models.CharField(max_length=256, verbose_name=_("Title"))


@reversion.register()
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
    description = models.TextField(_("Description"), blank=True, null=True, help_text=_("Describe the purpose of the milestone"))

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
            raise ValidationError(_(f"Given date is in the future"))

        # check start/target date
        if (self.start_date and self.target_date) and self.target_date < self.start_date:
            raise ValidationError(f"start_date({self.start_date}) > target_date({self.target_date})")

    def get_absolute_url(self):
        return f"{settings.URL_PREFIX}/admin/projects/kippomilestone/{self.id}/change/"

    @property
    def is_delayed(self):
        if not self.is_completed and not self.actual_date and self.target_date and self.target_date < timezone.now().date():
            return True
        return False

    @property
    def estimated_completion_date(self) -> datetime.datetime.date:
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
        return max(milestone_scheduled_effort_dates)

    def get_assignee_workdays(self, start_date: Optional[datetime.date] = None) -> Counter:
        if not start_date:
            current_datetime = timezone.now()
            start_datetime = datetime.datetime(current_datetime.year, current_datetime.month, 1, tzinfo=datetime.timezone.utc)
            current_date = start_datetime.date()

        # get organization memberships
        organization_memberships = list(
            OrganizationMembership.objects.filter(organization=self.project.organization, user__github_login__isnull=False, is_developer=True)
            .exclude(user__github_login__startswith=settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX)
            .order_by("user__github_login")
        )
        member_personal_holiday_dates = {m.user.github_login: tuple(m.user.personal_holiday_dates()) for m in organization_memberships}
        member_public_holiday_dates = {m.user.github_login: tuple(m.user.public_holiday_dates()) for m in organization_memberships}

        assignee_available_workdays = Counter()
        while current_date <= self.target_date:  # noqa
            for membership in organization_memberships:
                if (
                    current_date not in member_personal_holiday_dates[membership.user.github_login]
                    and current_date not in member_public_holiday_dates[membership.user.github_login]
                ):
                    if current_date.weekday() in membership.committed_weekdays:
                        assignee_available_workdays[membership.user] += 1
            current_date += datetime.timedelta(days=1)
        return assignee_available_workdays

    @property
    def available_work_days(self, start_date: Optional[datetime.date] = None) -> int:
        """Calculated the work days available considering the FULL OrganizationMembership available assignments"""
        assignee_available_workdays = self.get_assignee_workdays(start_date)
        total_available_workdays = sum(assignee_available_workdays.values())
        return total_available_workdays

    @property
    def estimated_work_days(self) -> int:
        """Return the effort days assigned to tasks in the given milestone"""
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
        cache_hash = get_ttlhash(seconds=60)
        if hasattr(self, "skip_cache") and self.skip_cache is True:  # for testing only
            # bust the cache so each call generates a new result
            import time

            cache_hash = time.time()

        project_developer_load, _, _ = get_projects_load(organization=self.project.organization, ttl_hash=cache_hash)

        # retrieve the number of estimated days assigned to this milestone
        total_milestone_assigned_workdays = 0
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
                            total_milestone_assigned_workdays += 1

        return total_milestone_assigned_workdays

    @property
    def tasks(self) -> QuerySet:
        return self.kippotask_milestone.all()  # reverse relation to KippoTask

    def update_github_milestones(self, user: Optional[KippoUser] = None, close: bool = False) -> List[Tuple[bool, object]]:
        """
        Create or Update related github milestones belonging to github repositories attached to the related project.
        :return:
            .. code:: python
                [
                    (CREATED, GithubMilestone Object),
                ]
        """
        github_milestones = []
        if not user:
            logger.warning(f"user object not given, using: {GITHUB_MANAGER_USERNAME}")
            user = KippoUser.objects.get(username=GITHUB_MANAGER_USERNAME)

        # collect existing
        existing_github_milestones_by_repo_html_url = {}
        existing_github_repositories_by_html_url = {}
        for github_repository in self.project.related_github_repositories():
            url = github_repository.html_url
            if url.endswith("/"):
                # remove to match returned result from github
                url = url[:-1]
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

    def __str__(self):
        return f"{self.__class__.__name__}({self.title})"

    class Meta:
        unique_together = ("project", "start_date", "target_date")


@receiver(pre_delete, sender=KippoMilestone)
def cleanup_github_milestones(sender, instance, **kwargs):
    """Close related Github milestones when  KippoMilestone is deleted."""
    try:
        related_github_milestone = GithubMilestone.objects.get(milestone=instance)
        if related_github_milestone:
            instance.update_github_milestones(close=True)
    except GithubMilestone.DoesNotExist:
        logger.info("no related GithubMilestone, will not attempt to close on github")


class ProjectAssignment(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.DO_NOTHING, related_name="projectassignment_project")
    user = models.ForeignKey("accounts.KippoUser", on_delete=models.DO_NOTHING, related_name="projectassignment_user")
    percentage = models.SmallIntegerField(
        help_text=_("Workload percentage assigned to project from available workload available for project organization")
    )


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


VALID_COLLECTISSUESPROJECTRESULT_STATES = (("processing", "processing"), ("complete", "complete"))


class CollectIssuesProjectResult(models.Model):
    action = models.ForeignKey(CollectIssuesAction, on_delete=models.CASCADE)
    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE)
    state = models.CharField(max_length=10, choices=VALID_COLLECTISSUESPROJECTRESULT_STATES, default="processing")
    new_task_count = models.PositiveSmallIntegerField(default=0)
    new_taskstatus_count = models.PositiveSmallIntegerField(default=0)
    updated_taskstatus_count = models.PositiveSmallIntegerField(default=0)
    unhandled_issues = JSONField()
