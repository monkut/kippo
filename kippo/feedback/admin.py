from commons.admin import UserCreatedBaseModelAdmin
from django.contrib import admin
from django.http import request as DjangoRequest  # noqa: N812

from .definitions import SUPERUSER_ONLY_FIELDS
from .models import Feedback


@admin.register(Feedback)
class FeedbackAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        "title",
        "category",
        "organization",
        "created_by",
        "created_datetime",
        "reviewed_datetime",
        "review_action",
    )
    list_filter = ("category", "organization", "review_action")
    search_fields = ("title", "comment")
    list_select_related = ("created_by", "organization")
    readonly_fields = ("created_by", "updated_by", "created_datetime", "updated_datetime", "closed_datetime")

    def get_readonly_fields(self, request: DjangoRequest, obj: Feedback | None = None) -> tuple[str, ...]:
        base = tuple(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            return base + SUPERUSER_ONLY_FIELDS
        return base
