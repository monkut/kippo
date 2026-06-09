from django.urls import include, path
from rest_framework_nested import routers

from .viewsets import (
    AssumptionEvaluationViewSet,
    BusinessRequirementEvaluationViewSet,
    ProblemDefinitionEvaluationViewSet,
    ProjectAssumptionViewSet,
    ProjectBusinessRequirementCategoryViewSet,
    ProjectBusinessRequirementCommentViewSet,
    ProjectBusinessRequirementEstimateViewSet,
    ProjectBusinessRequirementViewSet,
    ProjectProblemDefinitionCommentViewSet,
    ProjectProblemDefinitionViewSet,
    ProjectTechnicalRequirementCategoryViewSet,
    ProjectTechnicalRequirementCommentViewSet,
    ProjectTechnicalRequirementGithubIssueViewSet,
    ProjectTechnicalRequirementViewSet,
    ScheduleEstimationAPIView,
    TechnicalRequirementEvaluationViewSet,
)

router = routers.DefaultRouter()
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

# Nested under problem-definitions
pd_router = routers.NestedDefaultRouter(router, r"problem-definitions", lookup="problem_definition")
pd_router.register(r"comments", ProjectProblemDefinitionCommentViewSet, basename="problem-definition-comment")
pd_router.register(r"evaluations", ProblemDefinitionEvaluationViewSet, basename="problem-definition-evaluation")

# Nested under assumptions
a_router = routers.NestedDefaultRouter(router, r"assumptions", lookup="assumption")
a_router.register(r"evaluations", AssumptionEvaluationViewSet, basename="assumption-evaluation")

# Nested under business-requirements
br_router = routers.NestedDefaultRouter(router, r"business-requirements", lookup="business_requirement")
br_router.register(r"comments", ProjectBusinessRequirementCommentViewSet, basename="business-requirement-comment")
br_router.register(r"evaluations", BusinessRequirementEvaluationViewSet, basename="business-requirement-evaluation")

# Nested under technical-requirements
tr_router = routers.NestedDefaultRouter(router, r"technical-requirements", lookup="technical_requirement")
tr_router.register(r"comments", ProjectTechnicalRequirementCommentViewSet, basename="technical-requirement-comment")
tr_router.register(r"estimates", ProjectBusinessRequirementEstimateViewSet, basename="estimate")
tr_router.register(r"github-issues", ProjectTechnicalRequirementGithubIssueViewSet, basename="github-issue")
tr_router.register(r"evaluations", TechnicalRequirementEvaluationViewSet, basename="technical-requirement-evaluation")

# REST API URLs (to be included under /api/requirements/ in main urls.py)
api_patterns = [
    path("", include(router.urls)),
    path("", include(pd_router.urls)),
    path("", include(a_router.urls)),
    path("", include(br_router.urls)),
    path("", include(tr_router.urls)),
    path("schedule-estimation/", ScheduleEstimationAPIView.as_view(), name="schedule-estimation"),
]

urlpatterns = []
