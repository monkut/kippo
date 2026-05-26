"""Custom permissions for Projects API."""

from typing import Any

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView


def _user_org_ids(user: Any) -> set:  # noqa: ANN401
    """Return the set of organization PKs the user is a member of.

    Returns an empty set for unauthenticated users or users without
    `organizationmembership_set` (mirrors the queryset-scoping pattern in
    `projects/viewsets.py:90-94`).
    """
    if not (user and user.is_authenticated):
        return set()
    if not hasattr(user, "organizationmembership_set"):
        return set()
    return set(user.organizationmembership_set.values_list("organization", flat=True))


class IsSuperuserOrOwnOrgReadUpdateCreate(permissions.BasePermission):
    """
    Org-scoped permission for KippoProject (#284).

    - Read (GET/HEAD/OPTIONS): authenticated; queryset-level filtering keeps
      results to user's orgs.
    - Create (POST): authenticated AND `request.data["organization"]` is in
      the user's orgs (or superuser).
    - Update (PUT/PATCH): authenticated; object-level check enforces the
      target project belongs to one of the user's orgs (or superuser).
    - Delete (DELETE): superuser only.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:  # noqa: PLR0911
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if user.is_superuser:
            return True
        if request.method == "POST":
            target_org = request.data.get("organization") if hasattr(request, "data") else None
            if not target_org:
                return False
            return str(target_org) in {str(oid) for oid in _user_org_ids(user)}
        # PUT/PATCH: allow at view-level; object-level check enforces org membership.
        # DELETE/other: not permitted for non-superusers.
        return request.method in ["PUT", "PATCH"]

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:  # noqa: ANN401
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.is_superuser:
            return True

        if request.method in permissions.SAFE_METHODS:
            return True

        if request.method in ["PUT", "PATCH"]:
            return getattr(obj, "organization_id", None) in _user_org_ids(user)

        if request.method == "DELETE":
            return False

        return False


class IsSuperuserOrReadUpdateOnly(permissions.BasePermission):
    """
    Custom permission to only allow superusers to create or delete objects.
    Regular authenticated users can read and update.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        # Allow authenticated users to read (GET, HEAD, OPTIONS)
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated

        # Allow authenticated users to update (PUT, PATCH)
        if request.method in ["PUT", "PATCH"]:
            return request.user and request.user.is_authenticated

        # Only superusers can create (POST) or delete (DELETE)
        if request.method in ["POST", "DELETE"]:
            return request.user and request.user.is_authenticated and request.user.is_superuser

        return False

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:  # noqa: ANN401
        # Allow authenticated users to read
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated

        # Allow authenticated users to update
        if request.method in ["PUT", "PATCH"]:
            return request.user and request.user.is_authenticated

        # Only superusers can delete
        if request.method == "DELETE":
            return request.user and request.user.is_authenticated and request.user.is_superuser

        return False


class IsSuperuserOrReadUpdateCreateOwn(permissions.BasePermission):
    """
    Permission for WeeklyEffort: authenticated users can read, update, and create their own entries.
    Only superusers can delete.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        # Allow authenticated users to read (GET, HEAD, OPTIONS)
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated

        # Allow authenticated users to create (POST) and update (PUT, PATCH)
        if request.method in ["POST", "PUT", "PATCH"]:
            return request.user and request.user.is_authenticated

        # Only superusers can delete (DELETE)
        if request.method == "DELETE":
            return request.user and request.user.is_authenticated and request.user.is_superuser

        return False

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:  # noqa: ANN401
        # Allow authenticated users to read
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated

        # Allow users to update their own entries
        if request.method in ["PUT", "PATCH"]:
            if hasattr(obj, "user"):
                return request.user and request.user.is_authenticated and obj.user == request.user
            return request.user and request.user.is_authenticated

        # Only superusers can delete
        if request.method == "DELETE":
            return request.user and request.user.is_authenticated and request.user.is_superuser

        return False
