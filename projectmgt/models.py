from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import ugettext as _
import reversion
from tagging.registry import register

# TODO: consider setting user name on reversion save automatically?

@reversion.register
class Project(models.Model):
    name = models.CharField(max_length=256, help_text=_('Project Name'))
    by = models.ForeignKey(User, help_text=_('Created By User'))
    description = models.TextField(help_text=_("Description"))

    @property
    def updated_datetime(self):
        # get the latest revision
        # --> latest versions first (from reversion documentation)
        latest_version = reversion.get_for_object(self)[0]
        latest_created_datetime = latest_version.field_dict['created_datetime']
        return latest_created_datetime

    @property
    def created_datetime(self):
        # get the first revision datetime
        # --> latest versions first (from reversion documentation)
        initial_version = reversion.get_for_object(self)[-1]
        initial_created_datetime = initial_version.field_dict['created_datetime']
        return initial_created_datetime


@reversion.register
class Release(models.Model):  # Add django revision
    project = models.ForeignKey(Project)
    by = models.ForeignKey(User, help_text=_('Created By User'))

    @property
    def updated_datetime(self):
        # get the latest revision
        # --> latest versions first (from reversion documentation)
        latest_version = reversion.get_for_object(self)[0]
        latest_created_datetime = latest_version.field_dict['created_datetime']
        return latest_created_datetime

    @property
    def created_datetime(self):
        # get the first revision datetime
        # --> latest versions first (from reversion documentation)
        initial_version = reversion.get_for_object(self)[-1]
        initial_created_datetime = initial_version.field_dict['created_datetime']
        return initial_created_datetime


@reversion.register
class Task(models.Model):  # add django revision
    release = models.ForeignKey(Release)
    by = models.ForeignKey(User, help_text=_('Created By User'))
    hours_left = models.PositiveSmallIntegerField()
    description = models.TextField()
    # TODO: Add tags

    @property
    def updated_datetime(self):
        # get the latest revision
        # --> latest versions first (from reversion documentation)
        latest_version = reversion.get_for_object(self)[0]
        latest_created_datetime = latest_version.field_dict['created_datetime']
        return latest_created_datetime

    @property
    def created_datetime(self):
        # get the first revision datetime
        # --> latest versions first (from reversion documentation)
        initial_version = reversion.get_for_object(self)[-1]
        initial_created_datetime = initial_version.field_dict['created_datetime']
        return initial_created_datetime

    def __str__(self):
        if '\n' not in self.description:
            first_line = self.description
        else:
            first_line = self.description.split('\n')[0]
        return '{}[{}]({})'.format(_(self.__name__),  # get class name for translation
                                   self.id,
                                   first_line[:15])
register(Task)  # tagging model register

@reversion.register
class TaskComment(models.Model):
    task = models.ForeignKey(Task)
    by = models.ForeignKey(User, help_text=_('Created By User'))
    description = models.TextField()

    @property
    def updated_datetime(self):
        # get the latest revision
        # --> latest versions first (from reversion documentation)
        latest_version = reversion.get_for_object(self)[0]
        latest_created_datetime = latest_version.field_dict['created_datetime']
        return latest_created_datetime

    @property
    def created_datetime(self):
        # get the first revision datetime
        # --> latest versions first (from reversion documentation)
        initial_version = reversion.get_for_object(self)[-1]
        initial_created_datetime = initial_version.field_dict['created_datetime']
        return initial_created_datetime

    def __str__(self):
        if '\n' not in self.description:
            first_line = self.description
        else:
            first_line = self.description.split('\n')[0]
        return '{}[{}]({})'.format(_(self.__name__),  # get class name for translation
                                   self.id,
                                   first_line[:15])

