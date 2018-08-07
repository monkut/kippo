from django.conf.urls import url

from . import views


urlpatterns = [
    url('$', views.view_inprogress_task_status, name='view_inprogress_task_status')
]
