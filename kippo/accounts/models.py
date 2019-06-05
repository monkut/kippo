import logging
from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.validators import validate_email
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from common.models import UserCreatedBaseModel


logger = logging.getLogger(__name__)


class KippoOrganization(UserCreatedBaseModel):
    name = models.CharField(max_length=256)
    github_organization_name = models.CharField(max_length=100)
    day_workhours = models.PositiveSmallIntegerField(default=7,
                                                     help_text=_('Defines the number of hours in the workday'))
    default_task_category = models.CharField(max_length=256,
                                             default=settings.DEFAULT_KIPPOTASK_CATEGORY,
                                             null=True,
                                             blank=True,
                                             help_text=_('Default category to apply to KippoTask objects'))
    default_task_display_state = models.CharField(max_length=150,
                                                  default='in-progress',
                                                  help_text=_('Default Task STATE to show on initial task view'))
    default_columnset = models.ForeignKey('projects.ProjectColumnSet',
                                          on_delete=models.DO_NOTHING,
                                          null=True,
                                          default=None,
                                          blank=True,
                                          help_text=_('If defined, this will be set as the default ColumnSet when a Project is created'))
    default_labelset = models.ForeignKey('octocat.GithubRepositoryLabelSet',
                                         on_delete=models.DO_NOTHING,
                                         null=True,
                                         default=None,
                                         blank=True,
                                         help_text=_('If defined newly identified GithubRepositorie will AUTOMATICALLY have this LabelSet assigned'))

    @property
    def email_domains(self):
        domains = EmailDomain.objects.filter(organization=self)
        return domains

    def __str__(self):
        return f'{self.__class__.__name__}({self.name}-{self.github_organization_name})'

    class Meta:
        unique_together = ('name', 'github_organization_name')


class EmailDomain(UserCreatedBaseModel):
    organization = models.ForeignKey(KippoOrganization,
                                     on_delete=models.CASCADE)
    domain = models.CharField(max_length=255,
                              help_text=_('Organization email domains allowed to access organization information [USERNAME@{DOMAIN}]'))
    is_staff_domain = models.BooleanField(default=True,
                                          help_text=_('Domain has access to admin'))

    def clean(self):
        email_address_with_domain = f'test@{self.domain}'
        try:
            validate_email(email_address_with_domain)  # will raise ValidationError on failure
        except ValidationError:
            raise ValidationError(f'"{self.domain}" is not a valid EMAIL DOMAIN!')


class OrganizationMembership(UserCreatedBaseModel):
    organization = models.ForeignKey(
        KippoOrganization,
        on_delete=models.CASCADE,
        blank=True
    )
    email = models.EmailField(
        null=True,
        blank=True,
        help_text=_('Email address with Organization')
    )
    is_project_manager = models.BooleanField(default=False)
    is_developer = models.BooleanField(default=True)
    sunday = models.BooleanField(
        default=False,
        help_text=_('Works Sunday')
    )
    monday = models.BooleanField(
        default=True,
        help_text=_('Works Monday')
    )
    tuesday = models.BooleanField(
        default=True,
        help_text=_('Works Tuesday')
    )
    wednesday = models.BooleanField(
        default=True,
        help_text=_('Works Wednesday')
    )
    thursday = models.BooleanField(
        default=True,
        help_text=_('Works Thursday')
    )
    friday = models.BooleanField(
        default=True,
        help_text=_('Works Friday')
    )
    saturday = models.BooleanField(
        default=False,
        help_text=_('Works Saturday')
    )

    @property
    def email_domain(self):
        domain = self.email.split('@')[-1]  # NAME@DOMAIN.COM -> [ 'NAME', 'DOMAIN.COM']
        return domain

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)

        # check that given email matches expected organization email domain
        organization_domains = [d.domain for d in self.organization.email_domains]
        if self.email and self.email_domain not in organization_domains:
            raise ValidationError(f'Invalid email address ({self.email}) for organization({self.organization}) domains: {organization_domains}')


class KippoUser(AbstractUser):
    memberships = models.ManyToManyField(
        OrganizationMembership,
        blank=True,
        default=None
    )
    github_login = models.CharField(max_length=100,
                                    null=True,
                                    blank=True,
                                    default=None,
                                    help_text='Github Login username')
    is_github_outside_collaborator = models.BooleanField(default=False,
                                                         help_text=_('Set to True if User is an outside collaborator'))

    @property
    def display_name(self):
        return f'{self.last_name}, {self.first_name} ({self.github_login})'

    def personal_holiday_dates(self):
        for holiday in PersonalHoliday.objects.filter(user=self):
            holiday_start_date = holiday.day
            for days in range(holiday.duration):
                date = holiday_start_date + timezone.timedelta(days=days)
                yield date


class PersonalHoliday(models.Model):
    user = models.ForeignKey(KippoUser,
                             on_delete=models.CASCADE,
                             editable=False)
    created_datetime = models.DateTimeField(editable=False,
                                            auto_now_add=True)
    is_half = models.BooleanField(default=False,
                                  help_text=_('Select if taking only a half day'))
    day = models.DateField()
    duration = models.SmallIntegerField(default=1,
                                        help_text=_('How many days (including weekends/existing holidays)'))

    class Meta:
        ordering = ['-day']


def update_user_on_organizationmembership_add(signal, sender, **kwargs):
    """
    Update the user to is_staff=True, is_active=True, when added to an organization where organization.email_domain.is_staff_domain
    """
    user = kwargs['instance']
    action = kwargs['action']
    if kwargs['pk_set']:
        if action == 'post_add':
            # update user if organizationmembership.is_staff_domain == True
            memberships = kwargs['model'].objects.filter(pk__in=kwargs['pk_set'])
            is_staff = False
            for membership in memberships:
                logger.info(f'User({user}) added to {membership.organization}')
                for domain in membership.organization.email_domains:
                    if domain.is_staff_domain:
                        is_staff = True
                        break
                if is_staff:
                    break
            if is_staff:
                logger.info(f'Updating User({user}) is_staff/is_active -> True')
                user.is_staff = True
                user.is_active = True
                user.save()


models.signals.m2m_changed.connect(update_user_on_organizationmembership_add, KippoUser.memberships.through)
