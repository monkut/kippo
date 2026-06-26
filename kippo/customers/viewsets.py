from typing import Any

from accounts.models import KippoOrganization
from django.db.models import Prefetch, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from projects.models import KippoProject, KippoProjectBillingEntry
from projects.permissions import IsSuperuserOrReadUpdateCreateOwn
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.request import Request
from rest_framework.response import Response

from customers.functions import ACTIVE_PROJECT_COUNT, fiscal_year_org_summaries, project_received_total_current_fy, shift_fiscal_year
from customers.models import KippoCustomer
from customers.serializers import (
    CustomerActiveProjectSerializer,
    FiscalYearSummarySerializer,
    KippoCustomerSerializer,
)


def _active_projects_prefetch() -> Prefetch:
    """Active (open + display_as_active) projects with their contract + received billing entries —
    backing the list aggregates and the active-projects detail action. Mirrors the admin prefetch.
    """
    return Prefetch(
        "projects",
        queryset=(
            KippoProject.objects.filter(is_closed=False, display_as_active=True)
            .select_related("contract")
            .prefetch_related(Prefetch("contract__billing_entries", queryset=KippoProjectBillingEntry.objects.filter(is_received=True)))
            .order_by("name")
        ),
        to_attr="active_projects",
    )


class KippoCustomerViewSet(viewsets.ModelViewSet):
    """
    ViewSet for KippoCustomer model.

    **Organization Scoping:**
    - Regular users see only customers from organizations they belong to.
    - Superusers see all customers.

    **Filtering:**
    - organization: UUID filter (still intersected with user's memberships).
    - recent_ending: when ``true``, only customers with 1+ project whose ``target_date`` falls in the
      last two fiscal years (previous + current FY) of the customer's organization.
    - search: SearchFilter on `name`, `email`.

    **Permissions:**
    - Read (GET): Authenticated users (organization-scoped for regular users).
    - Create (POST): Authenticated users (organization-membership enforced by serializer).
    - Update (PUT/PATCH): Authenticated users (organization-scoped).
    - Delete (DELETE): Superusers only.
    """

    serializer_class = KippoCustomerSerializer
    permission_classes = [IsSuperuserOrReadUpdateCreateOwn]
    filter_backends = [SearchFilter]
    search_fields = ["name", "email"]
    queryset = KippoCustomer.objects.all().select_related("organization").order_by("organization", "name")

    @extend_schema(
        parameters=[
            OpenApiParameter(name="organization", description="Filter by organization UUID", required=False, type=str),
            OpenApiParameter(name="search", description="Search on name, email", required=False, type=str),
            OpenApiParameter(
                name="recent_ending",
                description="When true, only customers with 1+ project ending in the previous or current fiscal year.",
                required=False,
                type=OpenApiTypes.BOOL,
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def _scoped_organizations(self) -> "list[KippoOrganization]":
        """Organizations in scope for the current request (all for a superuser, else the user's)."""
        user = self.request.user
        if user.is_superuser:
            return list(KippoOrganization.objects.all())
        return list(user.organizations)

    def _recent_ending_filter(self) -> Q:
        """Q matching customers with 1+ project whose target_date falls in the previous-or-current
        fiscal year of the customer's own organization (mirrors CustomerEndingProjectsFilter).
        """
        query = Q(pk__in=[])  # matches nothing by default
        for organization in self._scoped_organizations():
            fiscal_year_start = organization.current_fiscal_year_start()
            window_start = shift_fiscal_year(fiscal_year_start, -1)
            window_end = shift_fiscal_year(fiscal_year_start, 1)
            query |= Q(
                organization=organization,
                projects__target_date__gte=window_start,
                projects__target_date__lt=window_end,
            )
        return query

    def get_queryset(self):
        """Org-scoped queryset with changelist-parity annotations/prefetches, the `organization`
        query param, and the `recent_ending` filter.
        """
        queryset = (
            super()
            .get_queryset()
            .select_related("compliance_check")
            .annotate(active_project_count=ACTIVE_PROJECT_COUNT)
            .prefetch_related(_active_projects_prefetch())
        )
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(organization__in=user_organizations)

        organization = self.request.query_params.get("organization", None)
        if organization:
            queryset = queryset.filter(organization=organization)

        if self.request.query_params.get("recent_ending", "").lower() == "true":
            queryset = queryset.filter(self._recent_ending_filter()).distinct()

        return queryset

    @extend_schema(responses=CustomerActiveProjectSerializer(many=True))
    # pagination_class=None → bare-array response (matches the runtime; no paginated {results} wrapper)
    @action(detail=True, methods=["get"], url_path="active-projects", pagination_class=None)
    def active_projects(self, request: Request, pk: str | None = None) -> Response:
        """List the customer's active (open + display_as_active) projects with contract amount, end
        date, and current-FY received-billing total (mirrors the admin's active-project detail rows).
        """
        customer = self.get_object()
        fiscal_year_start = customer.organization.current_fiscal_year_start()
        projects = getattr(customer, "active_projects", [])
        for project in projects:
            project.received_total_current_fy = project_received_total_current_fy(project, fiscal_year_start)
        serializer = CustomerActiveProjectSerializer(projects, many=True)
        return Response(serializer.data)

    @extend_schema(
        parameters=[
            OpenApiParameter(name="organization", description="Filter by organization UUID", required=False, type=str),
            OpenApiParameter(
                name="recent_ending",
                description="Restrict to customers with projects ending this/prev FY.",
                required=False,
                type=OpenApiTypes.BOOL,
            ),
        ],
        responses=FiscalYearSummarySerializer(many=True),
    )
    @action(detail=False, methods=["get"], url_path="fiscal-year-summary", pagination_class=None)
    def fiscal_year_summary(self, request: Request) -> Response:
        """Per-organization current-fiscal-year summary over the filtered in-scope customers (same
        org-scope + organization + recent_ending filters as list).
        """
        customers = self.filter_queryset(self.get_queryset())
        summaries = fiscal_year_org_summaries(customers)
        payload = [
            {
                "organization": {"id": summary["organization"].id, "name": summary["organization"].name},
                "fiscal_year_start": summary["fiscal_year_start"],
                "fiscal_year_end": summary["fiscal_year_end"],
                "customer_count": summary["customer_count"],
                "project_count": summary["project_count"],
                "planned_total": summary["planned_total"],
                "received_total": summary["received_total"],
                "monthly_planned_breakdown": summary["monthly_planned_breakdown"],
            }
            for summary in summaries
        ]
        serializer = FiscalYearSummarySerializer(payload, many=True)
        return Response(serializer.data)
