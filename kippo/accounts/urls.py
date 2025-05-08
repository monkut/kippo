from django.urls import path

from . import views

urlpatterns = [path("members/", views.view_organization_members, name="view_organization_members")]
