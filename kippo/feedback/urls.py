from django.urls import include, path
from rest_framework import routers

from .viewsets import FeedbackViewSet

router = routers.DefaultRouter()
router.register(r"feedback", FeedbackViewSet, basename="feedback")

api_patterns = [
    path("", include(router.urls)),
]

urlpatterns = []
