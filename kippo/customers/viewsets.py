from typing import Any

from drf_spectacular.utils import OpenApiParameter, extend_schema
from projects.permissions import IsSuperuserOrReadUpdateCreateOwn
from rest_framework import viewsets
from rest_framework.filters import SearchFilter
from rest_framework.request import Request
from rest_framework.response import Response

from customers.models import KippoCustomer
from customers.serializers import KippoCustomerSerializer


class KippoCustomerViewSet(viewsets.ModelViewSet):
    """
    ViewSet for KippoCustomer model.

    **Organization Scoping:**
    - Regular users see only customers from organizations they belong to.
    - Superusers see all customers.

    **Filtering:**
    - organization: UUID filter (still intersected with user's memberships).
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
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Filter by user organization memberships, plus the `organization` query param."""
        queryset = super().get_queryset()
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(organization__in=user_organizations)

        organization = self.request.query_params.get("organization", None)
        if organization:
            queryset = queryset.filter(organization=organization)

        return queryset
