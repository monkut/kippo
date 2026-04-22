from rest_framework import serializers

from .definitions import SUPERUSER_ONLY_FIELDS
from .models import Feedback

_BASE_READ_ONLY = ("created_by", "created_datetime", "updated_datetime", "closed_datetime")


class FeedbackSerializer(serializers.ModelSerializer):
    """Default serializer. Review fields are read-only for non-superusers."""

    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = Feedback
        fields = [
            "id",
            "category",
            "title",
            "comment",
            "organization",
            *SUPERUSER_ONLY_FIELDS,
            "created_by",
            "created_by_username",
            "created_datetime",
            "updated_datetime",
            "closed_datetime",
        ]
        read_only_fields = [*_BASE_READ_ONLY, *SUPERUSER_ONLY_FIELDS]


class FeedbackSuperuserSerializer(FeedbackSerializer):
    """Superuser variant: review fields are writable."""

    class Meta(FeedbackSerializer.Meta):
        read_only_fields = list(_BASE_READ_ONLY)
