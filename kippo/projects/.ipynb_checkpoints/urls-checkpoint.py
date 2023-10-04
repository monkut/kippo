from django.conf.urls import url
from django.urls import path

from . import views

urlpatterns = [
    url(
        "set/organization/(?P<organization_id>[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12})/$",
        views.set_user_session_organization,
        name="set_session_organization_id",
    ),
    path(
        "milestones/<uuid:milestone_id>/",
        views.view_milestone_status,
        name="view_milestone_status_single",
    ),
    path(
        "milestones/",
        views.view_milestone_status,
        name="view_milestone_status",
    ),
    path("download/", views.data_download_waiter, name="download_waiter"),
    path("download/done/", views.data_download_done, name="download_done"),
    url("$", views.view_inprogress_projects_status, name="view_project_status"),
]
