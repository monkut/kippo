from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig


class CommonConfig(AppConfig):
    name = 'common'


class KippoAdminConfig(AdminConfig):
    default_site = 'common.admin.KippoAdminSite'
