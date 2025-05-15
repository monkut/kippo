import json
import logging

from accounts.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from accounts.models import OrganizationMembership
from commons.admin import PrettyJSONWidget
from django.contrib import admin, messages
from django.db.models import JSONField, Q, QuerySet
from django.http import request as DjangoRequest  # noqa: N812
from django.utils.html import format_html, mark_safe
from django.utils.translation import gettext_lazy as _

from .functions import process_webhookevent_ids, update_repository_labels
from .models import GithubMilestone, GithubRepository, GithubRepositoryLabelSet, GithubWebhookEvent

logger = logging.getLogger(__name__)


@admin.register(GithubRepository)
class GithubRepositoryAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = ("name", "get_label_set_name", "get_html_url", "api_url")
    actions = ("update_labels",)
    ordering = ("organization", "name")

    def has_module_permission(self, request: DjangoRequest):
        has_permission = super().has_module_permission(request)
        if request.user.is_superuser:
            return True
        if has_permission and request.user.is_authenticated:
            # Check if the user is a project manager or developer in any of their organizations,
            # if so allow viewing of octocat apps
            is_participating_member = (
                OrganizationMembership.objects.filter(user=request.user, organization__in=request.user.organizations)
                .filter(Q(is_project_manager=True) | Q(is_developer=True))
                .exists()
            )
            if is_participating_member:
                return True
        return False

    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        """Limit results by user organizationmemberships"""
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations).distinct()

    def get_label_set_name(self, obj: GithubRepository) -> str:
        result = ""
        if obj.label_set:
            result = obj.label_set.name
        return result

    get_label_set_name.short_description = _("Label Set")

    def update_labels(self, request: DjangoRequest, queryset: QuerySet[GithubRepository]) -> None:
        delete = False  # Do not delete existing labels atm
        for kippo_repository in queryset:
            if not kippo_repository.label_set:
                msg = f"No GithubRepositoryLabelSet defined for ({kippo_repository.name}) cannot update labels!"
                self.message_user(request, msg, level=messages.ERROR)
            else:
                github_organization_name = kippo_repository.organization.github_organization_name
                githubaccesstoken = kippo_repository.organization.githubaccesstoken
                label_definitions = tuple(kippo_repository.label_set.labels)
                update_repository_labels(
                    github_organization_name,
                    githubaccesstoken.token,
                    repository_name=kippo_repository.name,
                    label_definitions=label_definitions,
                    delete=delete,
                )
                msg = f"({kippo_repository.name}) updating labels with: {kippo_repository.label_set.name}"
                self.message_user(request, msg, level=messages.INFO)

    update_labels.short_description = _("Update Repository Labels")

    def get_html_url(self, obj: GithubRepository) -> str:
        url = ""
        if obj.html_url:
            url = format_html('<a href="{url}"></a>', url=obj.html_url)
        return url

    get_html_url.short_description = _("Repository URL")


@admin.register(GithubMilestone)
class GithubMilestoneAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = ("number", "get_kippomilestone_title", "get_githubrepository_name", "get_html_url", "api_url")

    def has_module_permission(self, request: DjangoRequest):
        has_permission = super().has_module_permission(request)
        if request.user.is_superuser:
            return True
        if has_permission and request.user.is_authenticated:
            # Check if the user is a project manager or developer in any of their organizations,
            # if so allow viewing of octocat apps
            is_participating_member = (
                OrganizationMembership.objects.filter(user=request.user, organization__in=request.user.organizations)
                .filter(Q(is_project_manager=True) | Q(is_developer=True))
                .exists()
            )
            if is_participating_member:
                return True
        return False

    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        """Limit results by user organizationmemberships"""
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(repository__organization__in=request.user.organizations).distinct()

    def get_kippomilestone_title(self, obj: GithubMilestone) -> str:
        result = ""
        if obj.milestone and obj.milestone.title:
            result = obj.milestone.title
        return result

    def get_githubrepository_name(self, obj: GithubMilestone) -> str:
        result = ""
        if obj.repository and obj.repository.name:
            result = obj.repository.name
        return result

    def get_html_url(self, obj: GithubMilestone) -> str:
        url = ""
        if obj.html_url:
            url = format_html('<a href="{url}">{url}</a>', url=obj.html_url)
        return url

    get_html_url.short_description = _("Milestone URL")


@admin.register(GithubRepositoryLabelSet)
class GithubRepositoryLabelSetAdmin(AllowIsStaffAdminMixin, admin.ModelAdmin):
    list_display = ("name", "get_label_count", "updated_datetime", "created_datetime")

    formfield_overrides = {JSONField: {"widget": PrettyJSONWidget}}

    def has_module_permission(self, request: DjangoRequest):
        has_permission = super().has_module_permission(request)
        if request.user.is_superuser:
            return True
        if has_permission and request.user.is_authenticated:
            # Check if the user is a project manager or developer in any of their organizations,
            # if so allow viewing of octocat apps
            is_participating_member = (
                OrganizationMembership.objects.filter(user=request.user, organization__in=request.user.organizations)
                .filter(Q(is_project_manager=True) | Q(is_developer=True))
                .exists()
            )
            if is_participating_member:
                return True
        return False

    def get_queryset(self, request: DjangoRequest) -> QuerySet:
        """Limit results by user organizationmemberships"""
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(Q(organization__in=request.user.organizations) | Q(organization__isnull=True)).distinct()

    def get_label_count(self, obj: GithubRepositoryLabelSet) -> int:
        result = ""
        if obj.labels:
            result = len(obj.labels)
        return result

    get_label_count.short_description = "Defined Label Count"

    def has_change_permission(self, request: DjangoRequest, obj: GithubRepositoryLabelSet | None = None) -> bool:
        if obj:
            return request.user.is_superuser or obj.organization in request.user.organizations
        return True


@admin.register(GithubWebhookEvent)
class GithubWebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "organization",
        "created_datetime",
        "updated_datetime",
        "event_type",
        "get_event_action",
        "state",
    )
    readonly_fields = (
        "id",
        "organization",
        "created_datetime",
        "updated_datetime",
        "event_type",
        "state",
        "get_pprint_event",
    )

    actions = ["process_webhook_events", "reset_webhook_events"]

    def has_module_permission(self, request: DjangoRequest):
        has_permission = super().has_module_permission(request)
        if request.user.is_superuser:
            return True
        if has_permission and request.user.is_authenticated:
            # Check if the user is a project manager or developer in any of their organizations,
            # if so allow viewing of octocat apps
            is_participating_member = (
                OrganizationMembership.objects.filter(user=request.user, organization__in=request.user.organizations)
                .filter(Q(is_project_manager=True) | Q(is_developer=True))
                .exists()
            )
            if is_participating_member:
                return True
        return False

    def get_pprint_event(self, obj: GithubWebhookEvent | None = None):
        result = ""
        if obj and obj.event:
            result = json.dumps(obj.event, indent=4, ensure_ascii=False, sort_keys=True)
            result_str = f"<pre>{result}</pre>"
            result = mark_safe(result_str)  # noqa: S308
        return result

    get_pprint_event.short_description = "event"

    def get_event_action(self, obj: GithubWebhookEvent | None = None):
        action = ""
        if obj and obj.event:
            action = obj.event.get("action", "")
        return action

    get_event_action.short_description = _("ACTION")

    def process_webhook_events(self, request: DjangoRequest, queryset: QuerySet[GithubWebhookEvent]) -> None:
        queryset = queryset.filter(state="unprocessed")
        # convert to ids for task processing
        webhookevent_ids = [wh.id for wh in queryset]
        msg = f"Processing GithubWebhookEvent(s): {', '.join(str(i) for i in webhookevent_ids)}"
        self.message_user(request, msg, level=messages.INFO)
        process_webhookevent_ids(webhookevent_ids)

    process_webhook_events.short_description = _("Process Selected Event(s)")

    def reset_webhook_events(self, request: DjangoRequest, queryset: QuerySet[GithubWebhookEvent]) -> None:
        queryset.update(state="unprocessed")

        msg = "Updated Selected GithubWebhookEvent(s)"
        self.message_user(request, msg, level=messages.INFO)

    reset_webhook_events.short_description = _("Reset Selected Event(s)")
