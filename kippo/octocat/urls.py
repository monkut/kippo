from django.urls import re_path

from . import views

urlpatterns = [
    re_path(
        "webhook/(?P<organization_id>[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89aAbB][a-f0-9]{3}-[a-f0-9]{12})/$",
        views.webhook,
        name="octocat_webhook",
    ),
    re_path(
        "webhook/(?P<organization_id>[A-F0-9]{8}-[A-F0-9]{4}-4[A-F0-9]{3}-[89aAbB][A-F0-9]{3}-[A-F0-9]{12})/$",
        views.webhook,
        name="octocat_webhook",
    ),
]
