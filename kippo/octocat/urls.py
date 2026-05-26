from django.urls import path, re_path

from . import views
from .viewsets import GithubRepositoryViewSet

github_repository_list = GithubRepositoryViewSet.as_view({"get": "list"})
github_repository_detail = GithubRepositoryViewSet.as_view({"get": "retrieve"})

# REST API URLs — mounted under /api/octocat/ in root kippo/urls.py (kippo#284)
api_patterns = [
    path("github-repositories/", github_repository_list, name="github-repository-list"),
    path("github-repositories/<uuid:pk>/", github_repository_detail, name="github-repository-detail"),
]

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
