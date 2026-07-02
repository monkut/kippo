"""Shared DRF viewset helpers for organization scoping.

Canonical home for the superuser-bypass / organization-membership filter that was
hand-copied across the projects, requirements, accounts, customers and octocat APIs.
"""

from typing import Any

from django.db.models import QuerySet
from rest_framework.request import Request


def organization_ids_for_user(user: Any) -> set:  # noqa: ANN401
    """Return the set of organization PKs the user belongs to.

    Empty set for unauthenticated users or users without ``organizationmembership_set``.
    Use this for non-viewset call sites (permissions, ad-hoc access checks). Viewsets
    should prefer :class:`OrganizationFilterMixin`.
    """
    if not (user and user.is_authenticated):
        return set()
    if not hasattr(user, "organizationmembership_set"):
        return set()
    return set(user.organizationmembership_set.values_list("organization", flat=True))


class OrganizationFilterMixin:
    """Filter a viewset queryset to the request user's organization memberships.

    Superusers bypass the filter. Users without ``organizationmembership_set`` are left
    unfiltered (preserves the historical per-viewset guard).

    ``organization_lookup`` is the full ORM path from the model to its organization FK
    (e.g. ``"organization"``, ``"project__organization"``, ``"requirement__project__organization"``,
    or ``"id"`` when the model *is* the organization). Override the class attribute or pass it
    per-call.
    """

    request: Request
    organization_lookup: str = "organization"

    def filter_by_organization(self, queryset: QuerySet, organization_lookup: str | None = None) -> QuerySet:
        user = self.request.user
        if not user.is_superuser and hasattr(user, "organizationmembership_set"):
            lookup = organization_lookup or self.organization_lookup
            user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(**{f"{lookup}__in": user_organizations})
        return queryset
