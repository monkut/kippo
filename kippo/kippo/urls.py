"""kippo URL Configuration

The `pathpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.0/topics/http/paths/

"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView

# remove "Site Admministration" text from admin
admin.site.index_title = ""

urlpatterns = [
    path("", include("social_django.urls", namespace="social")),
    path("accounts/", include("accounts.urls")),
    path("projects/", include("projects.urls")),
    path("tasks/", include("tasks.urls")),
    path("octocat/", include("octocat.urls")),
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url=f"{settings.URL_PREFIX}/admin")),
]
