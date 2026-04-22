from typing import Any

from django.db.models import Q, QuerySet
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.serializers import BaseSerializer

from .models import Feedback
from .serializers import FeedbackSerializer, FeedbackSuperuserSerializer


class FeedbackViewSet(viewsets.ModelViewSet):
    """ViewSet for user-submitted Feedback."""

    queryset = Feedback.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self) -> type[BaseSerializer]:
        if self.request.user.is_superuser:
            return FeedbackSuperuserSerializer
        return FeedbackSerializer

    def get_queryset(self) -> QuerySet[Feedback]:
        user = self.request.user
        qs = Feedback.objects.select_related("created_by", "organization")
        if user.is_superuser:
            return qs
        user_organizations = user.organizationmembership_set.values_list("organization_id", flat=True)
        return qs.filter(Q(created_by=user) | Q(organization__in=user_organizations))

    def perform_create(self, serializer: BaseSerializer[Any]) -> None:
        user = self.request.user
        extra: dict[str, Any] = {"created_by": user, "updated_by": user}
        if not serializer.validated_data.get("organization"):
            first_membership = user.organizationmembership_set.select_related("organization").first()
            if first_membership:
                extra["organization"] = first_membership.organization
        serializer.save(**extra)

    def perform_update(self, serializer: BaseSerializer[Any]) -> None:
        self._require_owner_or_superuser(serializer.instance)
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance: Feedback) -> None:
        self._require_owner_or_superuser(instance)
        instance.delete()

    def _require_owner_or_superuser(self, instance: Feedback) -> None:
        user = self.request.user
        if user.is_superuser or instance.created_by_id == user.pk:
            return
        raise PermissionDenied("Only the creator or a superuser may modify this feedback.")
