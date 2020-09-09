from django.conf.urls import url

from . import views

urlpatterns = [url("members/$", views.view_organization_members, name="view_organization_members")]
