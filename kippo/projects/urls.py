from django.conf.urls import url

from . import views

urlpatterns = [
    url(
        'schedule/(?P<project_id>[0-9]+)',
        views.view_projects_schedule,
        name='view_projects_schedule'
    ),
    url(
        'overview/$',
        views.view_inprogress_projects_overview,
        name='view_inprogress_projects_overview'
    ),
    url(
        'set/organization/(?P<organization_id>[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12})/$',
        views.set_user_session_organization,
        name='set_session_organization_id'
    ),
    url(
        '$',
        views.view_inprogress_projects_status,
        name='view_project_status'
    )

]
