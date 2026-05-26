"""kippo URL Configuration

The `pathpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.0/topics/http/paths/

"""

from commons.views import SPAView
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic.base import RedirectView
from feedback.urls import api_patterns as feedback_api_patterns
from octocat.urls import api_patterns as octocat_api_patterns
from projects.urls import api_patterns
from requirements.urls import api_patterns as requirements_api_patterns

# remove "Site Admministration" text from admin
admin.site.index_title = ""

urlpatterns = [
    path("", include("social_django.urls", namespace="social")),
    path("accounts/", include("accounts.urls")),
    path("projects/", include("projects.urls")),
    path("tasks/", include("tasks.urls")),
    path("octocat/", include("octocat.urls")),
    path("admin/", admin.site.urls),
    path("api/", include(api_patterns)),
    path("api/requirements/", include(requirements_api_patterns)),
    path("api/feedback/", include(feedback_api_patterns)),
    path("api/octocat/", include(octocat_api_patterns)),
    # SPA UI - catch all routes under /ui/ and serve index.html
    re_path(r"^ui/(?P<path>.*)$", SPAView.as_view(), name="spa-ui"),
    path("ui/", SPAView.as_view(), name="spa-ui-root"),
    path("", RedirectView.as_view(url=f"{settings.URL_PREFIX}/admin")),
]
