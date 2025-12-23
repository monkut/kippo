from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from . import api_views, views
from .views import (
    CurrentUserView,
    PublicTokenObtainPairView,
    PublicTokenRefreshView,
    SessionTokenView,
    WeeklyEffortExpectedHoursView,
)
from .viewsets import (
    KippoProjectViewSet,
    ProjectAssignmentRateViewSet,
    ProjectMonthlyAssignmentViewSet,
    ProjectWeeklyEffortViewSet,
)

# REST Framework router for API viewsets
router = DefaultRouter()
router.register(r"projects", KippoProjectViewSet, basename="kippoproject")
router.register(r"assignment-rates", ProjectAssignmentRateViewSet, basename="projectassignmentrate")
router.register(r"monthly-assignments", ProjectMonthlyAssignmentViewSet, basename="projectmonthlyassignment")

# Manually define weeklyeffort viewset URLs to nest under projects/
weeklyeffort_list = ProjectWeeklyEffortViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)

weeklyeffort_detail = ProjectWeeklyEffortViewSet.as_view(
    {
        "get": "retrieve",
        "put": "update",
        "patch": "partial_update",
        "delete": "destroy",
    }
)

# HTML Views and Legacy API
html_and_legacy_patterns = [
    # HTML Views
    re_path(
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
    path("project/<uuid:project_id>/status/", views.get_projectstatus_details, name="project_status_details"),
    # Legacy API (JSON responses, not REST framework)
    path("api/project/<uuid:project_id>/status/", api_views.project_status_api, name="project_status_api"),
]

# REST API URLs (under /api/ prefix in main urls.py)
api_patterns = [
    # JWT Authentication (public endpoints - no auth required)
    path("token/", PublicTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", PublicTokenRefreshView.as_view(), name="token_refresh"),
    # Session to JWT (for SSO users without passwords)
    path("token/from-session/", SessionTokenView.as_view(), name="token_from_session"),
    # Current user (works with session and JWT auth)
    path("auth/me/", CurrentUserView.as_view(), name="current_user"),
    # Weekly effort expected hours
    path("weekly-effort/expected-hours/", WeeklyEffortExpectedHoursView.as_view(), name="weekly-effort-expected-hours"),
    # OpenAPI Documentation
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
    # Weekly Effort endpoints (nested under projects/) - MUST come before router
    path("projects/weeklyeffort/", weeklyeffort_list, name="projectweeklyeffort-list"),
    path("projects/weeklyeffort/<int:pk>/", weeklyeffort_detail, name="projectweeklyeffort-detail"),
    # API ViewSets
    path("", include(router.urls)),
]

urlpatterns = html_and_legacy_patterns
