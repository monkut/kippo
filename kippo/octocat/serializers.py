"""Serializers for the octocat REST API (kippo#284)."""

from rest_framework import serializers

from .models import GithubRepository


class GithubRepositorySerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True, allow_null=True)

    class Meta:
        model = GithubRepository
        fields = [
            "id",
            "organization",
            "organization_name",
            "project",
            "project_name",
            "name",
            "html_url",
            "api_url",
            "label_set",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "organization_name",
            "project_name",
            "created_datetime",
            "updated_datetime",
        ]
        # Skip the auto-derived UniqueTogetherValidator(name, html_url, api_url) — the nested
        # POST endpoint upserts on this triple intentionally (kippo#284). DB-level
        # unique_together still applies via the model's Meta.
        validators = []
        extra_kwargs = {
            # Auto-set from the parent project in nested create; never required from the client.
            "organization": {"required": False},
            "project": {"required": False, "allow_null": True},
            "label_set": {"required": False, "allow_null": True},
        }
