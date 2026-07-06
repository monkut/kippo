"""Custom permissions for Projects API."""

from typing import Any

from commons.viewsets import organization_ids_for_user
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView


class IsSuperuserOrOrgMemberForCategory(permissions.BasePermission):
    """Org-scoped write permission for KippoProjectOrganizationCategory (kippo#48).

    - Read (GET/HEAD/OPTIONS): authenticated; queryset-level filtering scopes results to the
      user's orgs plus the global defaults.
    - Write on an org-scoped category (create/update/delete): superuser, OR any member
      (``OrganizationMembership``) of that category's organization.
    - Write on a global (``organization=null``) default: superuser only. An org member may add
      org-scoped categories alongside the globals but may not edit or delete a global default.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if user.is_superuser:
            return True
        if request.method == "POST":
            # A missing/blank organization means a global default -> superuser only (already returned above).
            target_org = request.data.get("organization") if hasattr(request, "data") else None
            if not target_org:
                return False
            return str(target_org) in {str(oid) for oid in organization_ids_for_user(user)}
        # PUT/PATCH/DELETE: object-level check enforces org membership.
        return request.method in ("PUT", "PATCH", "DELETE")

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:  # noqa: ANN401
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if user.is_superuser:
            return True
        # global default: superuser only
        if obj.organization_id is None:
            return False
        return obj.organization_id in organization_ids_for_user(user)


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
            return str(target_org) in {str(oid) for oid in organization_ids_for_user(user)}
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
            return getattr(obj, "organization_id", None) in organization_ids_for_user(user)

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
