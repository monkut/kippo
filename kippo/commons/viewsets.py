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

    The result is memoized on the user object (``user._organization_ids_cache``) so the
    repeated calls within one request (get_queryset + permissions + serializer validators)
    reuse a single query. The user object is instantiated per request, so this is
    request-scoped and Lambda-safe — it never persists across invocations.
    """
    if not (user and user.is_authenticated):
        return set()
    if not hasattr(user, "organizationmembership_set"):
        return set()
    cached = getattr(user, "_organization_ids_cache", None)
    if cached is None:
        cached = set(user.organizationmembership_set.values_list("organization", flat=True))
        user._organization_ids_cache = cached
    return cached


def pm_organization_ids_for_user(user: Any) -> set:  # noqa: ANN401
    """Return the organization PKs where the user is a project manager (``is_project_manager=True``).

    Same request-scoped memoization contract as :func:`organization_ids_for_user`; used to gate
    org-admin actions (e.g. project-category management, kippo#48) to an org's PMs.
    """
    if not (user and user.is_authenticated):
        return set()
    if not hasattr(user, "organizationmembership_set"):
        return set()
    cached = getattr(user, "_pm_organization_ids_cache", None)
    if cached is None:
        cached = set(user.organizationmembership_set.filter(is_project_manager=True).values_list("organization", flat=True))
        user._pm_organization_ids_cache = cached
    return cached


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
            # Reuse the request-scoped org-id cache when a prior helper call (permission check,
            # serializer validator) already populated it — so a detail request fetches the ids
            # once. When the cache is cold (a list request whose only org use is this filter),
            # fall back to the lazy membership queryset so Django inlines it as a SQL subquery
            # and adds no extra round-trip.
            user_organizations = getattr(user, "_organization_ids_cache", None)
            if user_organizations is None:
                user_organizations = user.organizationmembership_set.values_list("organization", flat=True)
            queryset = queryset.filter(**{f"{lookup}__in": user_organizations})
        return queryset
