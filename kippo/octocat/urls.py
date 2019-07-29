from django.conf.urls import url

from . import views

urlpatterns = [
    url(
        '(?i)webhook/(?P<organization_id>[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89aAbB][a-f0-9]{3}-[a-f0-9]{12})/$',
        views.webhook,
        name='octocat_webhook'
    ),
]
