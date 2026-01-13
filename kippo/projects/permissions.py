"""Custom permissions for Projects API."""

from typing import Any

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView


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
