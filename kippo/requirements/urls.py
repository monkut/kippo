from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .viewsets import (
    ProjectAssumptionViewSet,
    ProjectBusinessRequirementCategoryViewSet,
    ProjectBusinessRequirementCommentViewSet,
    ProjectBusinessRequirementEstimateViewSet,
    ProjectBusinessRequirementViewSet,
    ProjectProblemDefinitionViewSet,
    ProjectTechnicalRequirementCategoryViewSet,
    ProjectTechnicalRequirementCommentViewSet,
    ProjectTechnicalRequirementGithubIssueViewSet,
    ProjectTechnicalRequirementViewSet,
    ScheduleEstimationAPIView,
)

router = DefaultRouter()
router.register(r"problem-definitions", ProjectProblemDefinitionViewSet, basename="problem-definition")
router.register(r"assumptions", ProjectAssumptionViewSet, basename="assumption")
router.register(
    r"business-requirement-categories",
    ProjectBusinessRequirementCategoryViewSet,
    basename="business-requirement-category",
)
router.register(
    r"technical-requirement-categories",
    ProjectTechnicalRequirementCategoryViewSet,
    basename="technical-requirement-category",
)
router.register(r"business-requirements", ProjectBusinessRequirementViewSet, basename="business-requirement")
router.register(r"technical-requirements", ProjectTechnicalRequirementViewSet, basename="technical-requirement")
router.register(r"business-requirement-comments", ProjectBusinessRequirementCommentViewSet, basename="business-requirement-comment")
router.register(
    r"technical-requirement-comments",
    ProjectTechnicalRequirementCommentViewSet,
    basename="technical-requirement-comment",
)
router.register(r"estimates", ProjectBusinessRequirementEstimateViewSet, basename="estimate")
router.register(r"github-issues", ProjectTechnicalRequirementGithubIssueViewSet, basename="github-issue")

# REST API URLs (to be included under /api/requirements/ in main urls.py)
api_patterns = [
    path("", include(router.urls)),
    path("schedule-estimation/", ScheduleEstimationAPIView.as_view(), name="schedule-estimation"),
]

urlpatterns = []
