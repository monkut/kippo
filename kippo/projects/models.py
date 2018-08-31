import logging
from typing import List, Tuple
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from django.db.models import Max
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.contrib.postgres.fields import ArrayField
import reversion
from ghorgs.managers import GithubOrganizationManager
from common.models import UserCreatedBaseModel
from octocat.models import GithubRepository, GithubMilestone, GITHUB_MILESTONE_CLOSE_STATE

from .exceptions import ProjectColumnSetError


logger = logging.getLogger(__name__)

UNASSIGNED_USER_GITHUB_LOGIN = settings.UNASSIGNED_USER_GITHUB_LOGIN


def get_target_date_default():
    # TODO: update to take into account configured holidays
    return timezone.now() + timezone.timedelta(days=settings.DEFAULT_KIPPORPOJECT_TARGET_DATE_DAYS)


def category_prefixes_default():
    return ['category:', 'cat:']


def estimate_prefixes_default():
    return ['estimate:', 'est:']


class ProjectColumnSet(models.Model):  # not using userdefined model in order to make model definitions more portable
    name = models.CharField(max_length=256,
                            verbose_name=_('Project Column Set Name'))
    created_datetime = models.DateTimeField(auto_now_add=True,
                                            editable=False)
    updated_datetime = models.DateTimeField(auto_now=True,
                                            editable=False)
    label_category_prefixes = ArrayField(models.CharField(max_length=10, blank=True),
                                         null=True,
                                         blank=True,
                                         default=category_prefixes_default,
                                         help_text=_('Github Issue Labels Category Prefixes'))
    label_estimate_prefixes = ArrayField(models.CharField(max_length=10, blank=True),
                                         null=True,
                                         blank=True,
                                         default=estimate_prefixes_default,
                                         help_text=_('Github Issue Labels Estimate Prefixes'))

    def get_column_names(self):
        return [c.name for c in ProjectColumn.objects.filter(columnset=self).order_by('index')]

    def get_active_column_names(self):
        names = [c.name for c in ProjectColumn.objects.filter(columnset=self, is_active=True).order_by('index')]
        if not names:
            raise ProjectColumnSetError(f'{self} does not have any ACTIVE columns assigned!')
        return names

    def get_done_column_names(self):
        names = [c.name for c in ProjectColumn.objects.filter(columnset=self, is_done=True).order_by('index')]
        if not names:
            raise ProjectColumnSetError(f'{self} does not have any DONE columns assigned!')
        return names

    def __str__(self):
        return f'{self.__class__.__name__}({self.name})'


class ProjectColumn(models.Model):
    columnset = models.ForeignKey(ProjectColumnSet,
                                  on_delete=models.CASCADE)
    index = models.PositiveSmallIntegerField(_('Column Display Index'),
                                             default=None,
                                             blank=True,
                                             unique=True,
                                             help_text=_('Github Project Column Display Index (0 start)'))
    name = models.CharField(max_length=256,
                            verbose_name=_('Project Column Display Name'))
    is_active = models.BooleanField(default=False,
                                    help_text=_('Set to True if tasks in column are considered ACTIVE'))
    is_done = models.BooleanField(default=False,
                                  help_text=_('Set to True if tasks in column are considered DONE'))

    def clean(self):
        if self.is_active and self.is_done:
            raise ValidationError('(Invalid Configuration) Both is_active and is_done set to True!')

    def save(self, *args, **kwargs):
        # auto-increment if blank (Consider moving to admin)
        if not self.index and ProjectColumn.objects.filter(columnset=self.columnset).exists():
            # get max value of current and increment by 1
            max_index = ProjectColumn.objects.filter(columnset=self.columnset).aggregate(Max('index'))['index__max']
            self.index = max_index + 1
            logger.info(f'{str(self)} incrementing: {self.index}')
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.__class__.__name__}({self.columnset.name}-{self.name})'

    class Meta:
        unique_together = (
            ('columnset', 'name'),
            ('columnset', 'index')
        )


@reversion.register()
class KippoProject(UserCreatedBaseModel):
    organization = models.ForeignKey('accounts.KippoOrganization',
                                     on_delete=models.CASCADE,
                                     editable=False)
    name = models.CharField(max_length=256,
                            unique=True)
    slug = models.CharField(max_length=300,
                            unique=True,
                            editable=False)
    category = models.CharField(max_length=256,
                                default=settings.DEFAULT_KIPPOPROJECT_CATEGORY)
    columnset = models.ForeignKey(ProjectColumnSet,
                                  on_delete=models.DO_NOTHING,
                                  help_text=_('ProjectColumnSet to use if/when a related Github project is created through Kippo'))
    project_manager = models.ForeignKey('accounts.KippoUser',
                                        on_delete=models.CASCADE,
                                        null=True,
                                        blank=True,
                                        help_text=_('Project Manager assigned to the project'))
    is_closed = models.BooleanField(_('Project is Closed'),
                                    default=False,
                                    help_text=_('Manually set when project is complete'))
    display_as_active = models.BooleanField(_('Display as Active'),
                                            default=True,
                                            help_text=_('If True, project will be included in the ActiveKippoProject List'))
    github_project_url = models.URLField(_('Github Project URL'),
                                         null=True,
                                         blank=True)
    allocated_staff_days = models.PositiveIntegerField(null=True,
                                                       blank=True,
                                                       help_text=_('Estimated Staff Days needed for Project Completion'))
    start_date = models.DateField(_('Start Date'),
                                  null=True,
                                  blank=True,
                                  help_text=_('Date the Project requires engineering resources'))
    target_date = models.DateField(_('Target Finish Date'),
                                   null=True,
                                   blank=True,
                                   default=get_target_date_default,
                                   help_text=_('Date the Project is planned to be completed by.'))
    actual_date = models.DateField(_('Actual Completed Date'),
                                   null=True,
                                   blank=True,
                                   help_text=_('The date the project was actually completed on (not the initial target)'))
    problem_definition = models.TextField(_('Project Problem Definition'),
                                          null=True,
                                          blank=True,
                                          help_text=_('Define the problem that the project is set out to solve.'))

    def clean(self):
        if self.actual_date and self.actual_date > timezone.now().date():
            raise ValidationError(_('Given date is in the future'))

    def developers(self):
        from tasks.models import KippoTask
        return {t.assignee for t in KippoTask.filter(project=self,
                                                     assignee__is_developer=True).exclude(assignee__github_login=UNASSIGNED_USER_GITHUB_LOGIN)}

    def get_admin_url(self):
        return f'{settings.URL_PREFIX}/admin/projects/kippoproject/{self.id}/change'

    def get_absolute_url(self):
        return f'{settings.URL_PREFIX}/projects/?slug={self.slug}'

    def get_column_names(self) -> List[str]:
        """
        Get the column names for use in github project columns
        :return: Column names in expected order
        """
        if not self.columnset:
            raise ValueError(_(f'{self}.columnset not defined!'))
        return self.columnset.get_column_names()

    def active_milestones(self):
        today = timezone.now().date()
        return KippoMilestone.objects.filter(project=self, target_date__gte=today).order_by('-target_date')

    def related_github_repositories(self):
        return GithubRepository.objects.filter(project=self)

    @property
    def github_project_name(self):
        return self.name

    @property
    def github_project_description(self):
        description = (f"""project_manager: {self.project_manager.display_name}<br/>"""
                       f"""start_date: {self.start_date}                       <br/>"""
                       f"""end_date:   {self.target_date}                      <br/>""")
        return description

    def save(self, *args, **kwargs):
        if not self.id:
            # perform initial creation tasks
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.__class__.__name__}({self.name})'


class ActiveKippoProjectManager(models.Manager):

    def get_queryset(self):
        qs = super().get_queryset()

        # update so only 'active"
        qs = qs.filter(is_closed=False,
                       display_as_active=True)
        return qs


class ActiveKippoProject(KippoProject):
    objects = ActiveKippoProjectManager()

    class Meta:
        proxy = True


@reversion.register()
class KippoMilestone(UserCreatedBaseModel):
    """Provides milestone definition and mapping to a Github Repository Milestone"""
    project = models.ForeignKey(KippoProject,
                                on_delete=models.CASCADE,
                                verbose_name=_('Kippo Project'),
                                editable=False)
    title = models.CharField(max_length=256,
                             verbose_name=_('Title'))
    allocated_staff_days = models.PositiveSmallIntegerField(null=True,
                                                            blank=True,
                                                            help_text=_('Budget Allocated Staff Days'))
    is_completed = models.BooleanField(_('Is Completed'),
                                       default=False)
    start_date = models.DateField(_('Start Date'),
                                  null=True,
                                  blank=True,
                                  default=None,
                                  help_text=_('Milestone Start Date'))
    target_date = models.DateField(_('Target Date'),
                                   null=True,
                                   blank=True,
                                   default=None,
                                   help_text=_('Milestone Target Completion Date'))
    actual_date = models.DateField(_('Actual Date'),
                                   null=True,
                                   blank=True,
                                   default=None,
                                   help_text=_('Milestone Actual Completion Date'))
    description = models.TextField(_('Description'),
                                   blank=True,
                                   null=True,
                                   help_text=_('Describe the purpose of the milestone'))

    @property
    def github_state(self) -> str:
        """
        Mapping of KippoMilestone is_completed to github milestone state value
        Github valid states are: open, closed, or all
        https://developer.github.com/v3/issues/milestones/

        :return: ('open'|'closed')
        """
        return 'open' if not self.is_completed else 'closed'

    def clean(self):
        if self.actual_date and (self.actual_date > timezone.now().date()):
            raise ValidationError(_(f'Given date is in the future'))

        # check start/target date
        if (self.start_date and self.target_date) and self.target_date < self.start_date:
            raise ValidationError(f'start_date({self.start_date}) > target_date({self.target_date})')

    @property
    def is_delayed(self):
        if not self.is_completed and not self.actual_date and self.target_date and self.target_date < timezone.now().date():
            return True
        return False

    def update_github_milestones(self, close=False) -> List[Tuple[bool, object]]:
        """
        Create or Update related github milestones belonging to github repositories attached to the related project.
        :return:
            .. code:: python
                [
                    (CREATED, GithubMilestone Object),
                ]
        """
        github_milestones = []

        # collect existing
        existing_github_milestones_by_repo_html_url = {}
        existing_github_repositories_by_html_url = {}
        for github_repository in GithubRepository.objects.filter(project=self.project):
            url = github_repository.html_url
            if url.endswith('/'):
                # remove to match returned result from github
                url = url[:-1]
            existing_github_repositories_by_html_url[url] = github_repository
            for github_milestone in GithubMilestone.objects.filter(repository=github_repository):
                existing_github_milestones_by_repo_html_url[url] = github_milestone

        github_organization_name = self.project.organization.github_organization_name
        token = self.project.organization.githubaccesstoken.token
        manager = GithubOrganizationManager(organization=github_organization_name,
                                            token=token)

        # identify related github project and get related repository urls
        related_repository_html_urls = list(existing_github_repositories_by_html_url.keys())
        if not related_repository_html_urls:
            logger.warning(f'Related Repository URLS not found for Telos Project: {self.project.name}')
        else:
            for repository in manager.repositories():
                if repository.html_url in related_repository_html_urls:
                    print(f'Updating {repository.name} Milestones...')
                    created = False
                    github_state = self.github_state
                    if close:
                        github_state = GITHUB_MILESTONE_CLOSE_STATE
                    if repository.html_url in existing_github_milestones_by_repo_html_url:
                        github_milestone = existing_github_milestones_by_repo_html_url[repository.html_url]
                        _ = repository.update_milestone(title=self.title,
                                                        description=self.description,
                                                        due_on=self.target_date,
                                                        state=github_state,
                                                        number=github_milestone.number)
                    else:
                        # create
                        response = repository.create_milestone(title=self.title,
                                                               description=self.description,
                                                               due_on=self.target_date,
                                                               state=github_state)

                        # get number and create GithubMilestone entry
                        # milestone_content defined at:
                        # https://developer.github.com/v3/issues/milestones/#create-a-milestone
                        _, milestone_content = response
                        number = milestone_content['number']
                        api_url = milestone_content['url']
                        html_url = milestone_content['html_url']
                        github_repository = existing_github_repositories_by_html_url[repository.html_url]
                        github_milestone = GithubMilestone(milestone=self,
                                                           number=number,
                                                           repository=github_repository,
                                                           api_url=api_url,
                                                           html_url=html_url)
                        github_milestone.save()
                        created = True
                    action = 'create' if created else 'update'
                    print(f'+ {action} Github Milestone: ({repository.name}) {self.title}')
                    github_milestones.append((created, github_milestone))
        return github_milestones

    def save(self, *args, **kwargs):
        github_milestone_action = 'update'
        if not self.id:  # not defined only on initial creation!
            github_milestone_action = 'create'
            # assign project number
            existing_milestone_count = KippoMilestone.objects.filter(project=self.project).count()
            if existing_milestone_count > 1:
                # Milestones may be deleted, make sure to use a number that is not in use
                # use existing max number + 1
                max_project_number = KippoMilestone.objects.filter(project=self.project).aggregate(Max('number'))['number__max']
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

        if not settings.TESTING:
            # update related github milestones
            self.update_github_milestones()

        # update project on_target value on save
        # > Needs to be after save, since Project will query DB and used saved values for calculation
        self.project.update_ontarget_status()  # NOTE: May want to remove cron job that periodically updates this.

    def __str__(self):
        return f'{self.__class__.__name__}({self.title})'

    class Meta:
        unique_together = ('project', 'start_date', 'target_date')


@receiver(pre_delete, sender=KippoMilestone)
def cleanup_github_milestones(sender, instance, **kwargs):
    """Close related Github milestones when  KippoMilestone is deleted."""
    instance.update_github_milestones(close=True)

