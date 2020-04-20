"""kippo URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.0/topics/http/urls/

"""
from django.conf import settings
from django.conf.urls import include, url
from django.contrib import admin
from django.views.generic.base import RedirectView

urlpatterns = [
    url("", include("social_django.urls", namespace="social")),
    url("^accounts/", include("accounts.urls")),
    url("^projects/", include("projects.urls")),
    url("^tasks/", include("tasks.urls")),
    url("^octocat/", include("octocat.urls")),
    url(r"^$", RedirectView.as_view(url=f"{settings.URL_PREFIX}/admin")),
    url(r"^admin/", admin.site.urls),
]
