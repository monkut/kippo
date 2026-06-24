from typing import TYPE_CHECKING

from rest_framework import serializers

from customers.models import KippoCustomer

if TYPE_CHECKING:
    from accounts.models import KippoOrganization


class KippoCustomerSerializer(serializers.ModelSerializer):
    """Serializer for KippoCustomer model."""

    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = KippoCustomer
        fields = [
            "id",
            "organization",
            "organization_name",
            "name",
            "email",
            "phone",
            "website",
            "document_url",
            "notes",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["id", "organization_name", "created_datetime", "updated_datetime"]

    def validate_organization(self, value: "KippoOrganization") -> "KippoOrganization":
        request = self.context.get("request")
        if request is None or request.user.is_superuser:
            return value
        user_org_ids = set(request.user.organizationmembership_set.values_list("organization_id", flat=True))
        if value.id not in user_org_ids:
            raise serializers.ValidationError("You can only create/update customers in organizations you belong to.")
        return value
