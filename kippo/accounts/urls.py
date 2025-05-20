from django.urls import path

from . import views

urlpatterns = [
    path("members/", views.view_organization_members, name="view_organization_members"),
    path("slack/webhook/<uuid:organization_id>/slack/events", views.organization_slack_webhook, name="view_organization_slack_webhook"),
]
