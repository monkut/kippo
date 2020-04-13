import json
import logging
from typing import Dict, Tuple

from accounts.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from common.admin import PrettyJSONWidget
from django.contrib import admin, messages
from django.contrib.postgres.fields import JSONField
from django.db.models import Q
from django.utils.html import format_html, mark_safe
from django.utils.translation import ugettext_lazy as _
from ghorgs.managers import GithubOrganizationManager
from zappa.asynchronous import task

from .functions import process_webhookevent_ids
from .models import GithubMilestone, GithubRepository, GithubRepositoryLabelSet, GithubWebhookEvent

logger = logging.getLogger(__name__)


@task
def update_repository_labels(
    github_orgainization_name: str, token: str, repository_name: str, label_definitions: Tuple[Dict[str, str]], delete: bool = False
):
    deleted_labels = []
    created_labels = []
    github_manager = GithubOrganizationManager(organization=github_orgainization_name, token=token)
    repository_name_filter = (repository_name,)
    logger.debug(f"repository_name_filter: {repository_name_filter}")
    for ghorgs_repository in github_manager.repositories(names=repository_name_filter):
        existing_label_names = [label["name"] for label in ghorgs_repository.labels]

        # get label definitions
        for label_definition in label_definitions:
            ghorgs_repository.create_label(label_definition["name"], label_definition["description"], label_definition["color"])
            created_labels.append(label_definition)

        if delete:
            undefined_label_names = set(existing_label_names) - set(created_labels)
            for label_name in undefined_label_names:
                ghorgs_repository.delete_label(label_name)
                deleted_labels.append(label_name)
    logger.info(f"{github_orgainization_name} ({repository_name}) created: {created_labels}")
    logger.info(f"{github_orgainization_name} ({repository_name}) deleted: {deleted_labels}")


@admin.register(GithubRepository)
class GithubRepositoryAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = ("name", "get_label_set_name", "get_html_url", "api_url")
    actions = ("update_labels",)
    ordering = ("organization", "name")

    def get_queryset(self, request):
        """Limit results by user organizationmemberships"""
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=request.user.organizations).distinct()

    def get_label_set_name(self, obj):
        result = ""
        if obj.label_set:
            result = obj.label_set.name
        return result

    get_label_set_name.short_description = "Label Set"

    def update_labels(self, request, queryset):
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

    update_labels.short_description = "Update Repository Labels"

    def get_html_url(self, obj):
        url = ""
        if obj.html_url:
            url = format_html('<a href="{url}"></a>', url=obj.html_url)
        return url

    get_html_url.short_description = _("Repository URL")


@admin.register(GithubMilestone)
class GithubMilestoneAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = ("number", "get_kippomilestone_title", "get_githubrepository_name", "get_html_url", "api_url")

    def get_queryset(self, request):
        """Limit results by user organizationmemberships"""
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(repository__organization__in=request.user.organizations).distinct()

    def get_kippomilestone_title(self, obj):
        result = ""
        if obj.milestone and obj.milestone.title:
            result = obj.milestone.title
        return result

    def get_githubrepository_name(self, obj):
        result = ""
        if obj.repository and obj.repository.name:
            result = obj.repository.name
        return result

    def get_html_url(self, obj):
        url = ""
        if obj.html_url:
            url = format_html('<a href="{url}">{url}</a>', url=obj.html_url)
        return url

    get_html_url.short_description = _("Milestone URL")


@admin.register(GithubRepositoryLabelSet)
class GithubRepositoryLabelSetAdmin(AllowIsStaffAdminMixin, admin.ModelAdmin):
    list_display = ("name", "get_label_count", "updated_datetime", "created_datetime")

    formfield_overrides = {JSONField: {"widget": PrettyJSONWidget}}

    def get_queryset(self, request):
        """Limit results by user organizationmemberships"""
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(Q(organization__in=request.user.organizations) | Q(organization__isnull=True)).distinct()

    def get_label_count(self, obj):
        result = ""
        if obj.labels:
            result = len(obj.labels)
        return result

    get_label_count.short_description = "Defined Label Count"

    def has_change_permission(self, request, obj=None) -> bool:
        if obj:
            if request.user.is_superuser:
                return True
            elif obj.organization in request.user.organizations:
                return True
            else:
                return False
        else:
            return True


@admin.register(GithubWebhookEvent)
class GithubWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "created_datetime", "updated_datetime", "event_type", "get_event_action", "state")
    readonly_fields = ("id", "organization", "created_datetime", "updated_datetime", "event_type", "state", "get_pprint_event")

    actions = ["process_webhook_events", "reset_webhook_events"]

    def get_pprint_event(self, obj=None):
        result = ""
        if obj and obj.event:
            result = json.dumps(obj.event, indent=4, ensure_ascii=False, sort_keys=True)
            result_str = f"<pre>{result}</pre>"
            result = mark_safe(result_str)
        return result

    get_pprint_event.short_description = "event"

    def get_event_action(self, obj=None):
        action = ""
        if obj and obj.event:
            action = obj.event.get("action", "")
        return action

    get_event_action.short_description = _("ACTION")

    def process_webhook_events(self, request, queryset):
        queryset = queryset.filter(state="unprocessed")
        # convert to ids for task processing
        webhookevent_ids = [wh.id for wh in queryset]
        msg = f'Processing GithubWebhookEvent(s): {", ".join(str(i) for i in webhookevent_ids)}'
        self.message_user(request, msg, level=messages.INFO)
        process_webhookevent_ids(webhookevent_ids)

    process_webhook_events.short_description = _("Process Selected Event(s)")

    def reset_webhook_events(self, request, queryset):
        queryset.update(state="unprocessed")

        msg = f"Updated Selected GithubWebhookEvent(s)"
        self.message_user(request, msg, level=messages.INFO)

    reset_webhook_events.short_description = _("Reset Selected Event(s)")
