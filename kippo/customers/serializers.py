from decimal import Decimal
from typing import TYPE_CHECKING

from rest_framework import serializers

from customers.functions import active_projects_contract_total
from customers.models import KippoCustomer

if TYPE_CHECKING:
    from accounts.models import KippoOrganization


class KippoCustomerSerializer(serializers.ModelSerializer):
    """Serializer for KippoCustomer model.

    Changelist-parity read-only additions (kippo#45): ``active_project_count`` (annotated scalar
    Subquery in the viewset), ``active_projects_contract_total`` (Σ active projects' contract totals,
    from the prefetched active-projects set), and ``compliance_verified``.
    """

    organization_name = serializers.CharField(source="organization.name", read_only=True)
    active_project_count = serializers.IntegerField(read_only=True)
    active_projects_contract_total = serializers.SerializerMethodField()
    compliance_verified = serializers.SerializerMethodField()

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
            "contract_folder_url",
            "notes",
            "active_project_count",
            "active_projects_contract_total",
            "compliance_verified",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = [
            "id",
            "organization_name",
            "active_project_count",
            "active_projects_contract_total",
            "compliance_verified",
            "created_datetime",
            "updated_datetime",
        ]

    def get_active_projects_contract_total(self, obj: KippoCustomer) -> Decimal:
        # ``active_projects`` is prefetched by the viewset (to_attr); fall back to () so a detail/create
        # response (no prefetch) returns 0 rather than raising.
        return active_projects_contract_total(list(getattr(obj, "active_projects", ())))

    def get_compliance_verified(self, obj: KippoCustomer) -> bool:
        compliance_check = getattr(obj, "compliance_check", None)
        return bool(compliance_check and compliance_check.verified)

    def validate_organization(self, value: "KippoOrganization") -> "KippoOrganization":
        request = self.context.get("request")
        if request is None or request.user.is_superuser:
            return value
        user_org_ids = set(request.user.organizationmembership_set.values_list("organization_id", flat=True))
        if value.id not in user_org_ids:
            raise serializers.ValidationError("You can only create/update customers in organizations you belong to.")
        return value


class CustomerActiveProjectSerializer(serializers.Serializer):
    """One active project row for GET /api/customers/{id}/active-projects/ (mirrors the admin's
    expandable active-project detail). ``received_total_current_fy`` is set by the viewset.
    """

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    contract_amount = serializers.SerializerMethodField()
    contract_end_date = serializers.SerializerMethodField()
    received_total_current_fy = serializers.DecimalField(max_digits=14, decimal_places=0, coerce_to_string=False, read_only=True)

    def get_contract_amount(self, obj) -> Decimal | None:  # noqa: ANN001 (obj is a KippoProject; left unannotated so drf-spectacular need not import it)
        contract = getattr(obj, "contract", None)
        return contract.total_amount if contract else None

    def get_contract_end_date(self, obj) -> str | None:  # noqa: ANN001
        contract = getattr(obj, "contract", None)
        return contract.end_date.isoformat() if contract and contract.end_date else None


class FiscalYearMonthlyBreakdownSerializer(serializers.Serializer):
    """One FY-month planned-billing total: {"month": "YYYY/MM", "amount": decimal}."""

    month = serializers.CharField()
    amount = serializers.DecimalField(max_digits=16, decimal_places=0, coerce_to_string=False)


class FiscalYearSummaryOrganizationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class FiscalYearSummarySerializer(serializers.Serializer):
    """Per-organization current-fiscal-year summary for GET /api/customers/fiscal-year-summary/."""

    organization = FiscalYearSummaryOrganizationSerializer()
    fiscal_year_start = serializers.DateField()
    fiscal_year_end = serializers.DateField()
    customer_count = serializers.IntegerField()
    project_count = serializers.IntegerField()
    planned_total = serializers.DecimalField(max_digits=16, decimal_places=0, coerce_to_string=False)
    received_total = serializers.DecimalField(max_digits=16, decimal_places=0, coerce_to_string=False)
    monthly_planned_breakdown = FiscalYearMonthlyBreakdownSerializer(many=True)
