from django.db import models
from django.conf import settings


class UserCreatedBaseModel(models.Model):
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   on_delete=models.CASCADE,
                                   editable=False,
                                   related_name="%(app_label)s_%(class)s_created_by")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   on_delete=models.CASCADE,
                                   editable=False,
                                   related_name="%(app_label)s_%(class)s_updated_by")
    created_datetime = models.DateTimeField(auto_now_add=True,
                                            editable=False)
    updated_datetime = models.DateTimeField(auto_now=True,
                                            editable=False)
    closed_datetime = models.DateTimeField(editable=False,
                                           null=True)

    class Meta:
        abstract = True
