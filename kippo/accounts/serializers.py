"""Serializers for Accounts API."""

from rest_framework import serializers

from .models import PersonalHoliday


class PersonalHolidaySerializer(serializers.ModelSerializer):
    """Serializer for PersonalHoliday model.

    The `user` field is auto-set to the current authenticated user on create.
    Users can only create personal holidays for themselves.
    """

    user_username = serializers.CharField(source="user.username", read_only=True)
    user_display_name = serializers.SerializerMethodField()

    class Meta:
        model = PersonalHoliday
        fields = [
            "id",
            "user",
            "user_username",
            "user_display_name",
            "day",
            "is_half",
            "duration",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "user",
            "user_username",
            "user_display_name",
            "created_datetime",
            "updated_datetime",
        ]

    def get_user_display_name(self, obj: PersonalHoliday) -> str:
        """Get the user's display name."""
        user = obj.user
        if hasattr(user, "display_name"):
            return user.display_name
        return f"{user.first_name} {user.last_name}".strip() or user.username
