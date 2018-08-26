from django.conf.urls import url

from . import views

urlpatterns = [
    url('schedule/(?P<project_id>[0-9]+)', views.view_projects_schedule, name='view_projects_schedule'),
    url('overview/$', views.view_inprogress_projects_overview, name='view_inprogress_projects_overview'),
    url('$', views.view_inprogress_projects_status, name='project_status')

]
