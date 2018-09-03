import logging
from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import Group
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


class KippoUser(AbstractUser):
    organization = models.ForeignKey(KippoOrganization,
                                     on_delete=models.CASCADE,
                                     null=True,
                                     blank=True)
    is_project_manager = models.BooleanField(default=False)
    is_developer = models.BooleanField(default=True)
    github_login = models.CharField(max_length=100,
                                    null=True,
                                    blank=True,
                                    default=None,
                                    help_text='Github Login username')

    @property
    def email_domain(self):
        domain = self.email.split('@')[-1]  # NAME@DOMAIN.COM -> [ 'NAME', 'DOMAIN.COM']
        return domain

    @property
    def display_name(self):
        return f'{self.last_name}, {self.first_name} ({self.github_login})'

    def personal_holiday_dates(self):
        for holiday in PersonalHoliday.objects.filter(self=self):
            holiday_start_date = holiday.day
            for days in range(holiday.duration):
                date = holiday_start_date + timezone.timedelta(days=days)
                yield date

    def save(self, *args, **kwargs):
        is_initial = False
        # only update on initial creation
        # --> Will not have an ID on initial save
        if self.id is None:
            is_initial = True
            self.is_staff = True  # auto-add is_staff (so user can use the ADMIN)
            self.is_superuser = False
            if not settings.DEBUG:  # allow manually created users in development
                # find the organization for the given user
                try:
                    email_domain = EmailDomain.objects.get(domain=self.email_domain)
                    self.organization = email_domain.organization
                except EmailDomain.DoesNotExist:
                    raise PermissionDenied('Invalid Email Domain')
            else:
                logger.warning('')
        super().save(*args, **kwargs)


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
