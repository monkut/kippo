"""Serializers for Accounts API."""

from rest_framework import serializers

from .models import PersonalHoliday, PublicHoliday


class OrganizationSerializer(serializers.Serializer):
    """Minimal projection of a `KippoOrganization` for the org-listing endpoint.

    Used by `GET /api/organizations/` so kippo-ui can render an org picker
    without needing to embed the full admin model. Per kippo#14.
    """

    id = serializers.UUIDField(help_text="KippoOrganization primary key.")
    name = serializers.CharField()
    github_organization_name = serializers.CharField()


class OrganizationMemberDetailSerializer(serializers.Serializer):
    """Org-scoped projection of `KippoUser` joined with their `OrganizationMembership`.

    Returned by `GET /api/organizations/<id>/members/`. Adds the per-org PII
    (`email`, `slack_*`) that `projects.serializers.OrganizationMemberSerializer`
    intentionally omits — keep these two serializers separate so the per-project
    contract consumed by kippo-ui#57 is not coupled to this richer shape. Per kippo#14.
    """

    user_id = serializers.UUIDField(help_text="KippoUser primary key.")
    username = serializers.CharField()
    display_name = serializers.CharField(help_text="Composed first + last + (github_login).")
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    email = serializers.CharField(allow_blank=True, help_text="Per-org email from OrganizationMembership, not KippoUser.")
    github_login = serializers.CharField(allow_blank=True)
    is_developer = serializers.BooleanField()
    is_project_manager = serializers.BooleanField()
    slack_username = serializers.CharField(allow_blank=True)
    slack_user_id = serializers.CharField(allow_blank=True)
    slack_image_url = serializers.CharField(allow_blank=True)
    available_work_days = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text=(
            "Workdays this member is available in the calendar month requested via "
            "the `month` query parameter (committed weekdays minus public/personal "
            "holidays). Null/absent when `month` is not provided."
        ),
    )


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


class PublicHolidaySerializer(serializers.ModelSerializer):
    """Serializer for PublicHoliday model.

    Returns public holidays for the authenticated user's holiday_country.
    """

    country_name = serializers.CharField(source="country.name", read_only=True)

    class Meta:
        model = PublicHoliday
        fields = [
            "id",
            "name",
            "day",
            "country_name",
        ]
        read_only_fields = fields
