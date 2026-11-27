"""Admin actions for the project changelists."""

import datetime
import logging
import re
import urllib.parse

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import models
from django.http import (
    HttpResponseRedirect,
    request as DjangoRequest,  # noqa: N812
)
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from octocat.functions import copy_project_v2, create_project_v2, get_organization_id
from slack_sdk.errors import SlackApiError
from tasks.periodic.tasks import collect_github_project_issues

from ..definitions import (
    CONTINUATION_LEAD_SOURCE_VALUE,
)
from ..exceptions import GithubMilestoneAlreadyExistsError, SlackChannelNotFoundError
from ..functions import (
    get_user_session_organization,
)
from ..models import (
    PHASE_COMPLETED,
    KippoProject,
    KippoProjectStatus,
    participant_period_error,
)
from ..services.participants import create_remaining_assignments, remaining_months
from .definitions import CLOSE_ORIGIN_PARAM, CLOSE_PROJECT_NO_CONTINUATION_VALUE, CONTINUATION_SOURCE_PARAM, CONTINUATION_SOURCE_VALUE

logger = logging.getLogger(__name__)


def create_github_organizational_project_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:
    """
    Admin Action command to create a GitHub organizational project (ProjectsV2) from the selected KippoProject(s).

    Uses the GitHub ProjectsV2 API. If the organization has a default_github_project_template configured,
    the new project will be created by copying that template. Otherwise, a blank project is created.
    """
    successful_creation_projects = []
    skipping = []
    errors = []
    created_without_template = []
    for kippo_project in queryset:
        if kippo_project.github_project_html_url:
            message = f"{kippo_project.name} already has GitHub Project set ({kippo_project.github_project_html_url}), SKIPPING!"
            logger.warning(message)
            skipping.append(message)
            continue

        try:
            github_organization_name = kippo_project.organization.github_organization_name
            githubaccesstoken = kippo_project.organization.githubaccesstoken
            token = githubaccesstoken.token

            # Get organization node ID required for ProjectsV2 mutations
            org_id = get_organization_id(github_organization_name, token)
            logger.debug(f"Organization ID for {github_organization_name}: {org_id}")

            # Use the project name for the GitHub project title
            project_title = kippo_project.name

            # Check if a template is configured
            template_id = kippo_project.organization.default_github_project_template
            if template_id:
                # Create project by copying the template
                logger.info(f"Creating ProjectsV2 from template {template_id} for {kippo_project.name}")
                project_data = copy_project_v2(template_id, org_id, project_title, token)
            else:
                # Create a blank project
                logger.warning(f"No template configured for {github_organization_name}, creating blank ProjectsV2 for {kippo_project.name}")
                project_data = create_project_v2(org_id, project_title, token)
                created_without_template.append(kippo_project.name)

            # Update KippoProject with the new project URLs
            kippo_project.github_project_html_url = project_data["url"]
            # ProjectsV2 uses GraphQL node IDs instead of REST API URLs
            kippo_project.github_project_api_nodeid = project_data["id"]
            kippo_project.save()

            logger.info(f"Created GitHub ProjectsV2: {project_data['url']}")
            successful_creation_projects.append((kippo_project.name, project_data["url"]))

        except Exception as e:
            error_msg = f"Failed to create GitHub Project for {kippo_project.name}: {e}"
            logger.exception(error_msg)
            errors.append(error_msg)

    if skipping:
        for m in skipping:
            modeladmin.message_user(request, message=m, level=messages.WARNING)
    if created_without_template:
        modeladmin.message_user(
            request,
            message=f"No default_github_project_template configured for organization. Empty project(s) created: {created_without_template}",
            level=messages.WARNING,
        )
    if errors:
        for m in errors:
            modeladmin.message_user(request, message=m, level=messages.ERROR)
    if successful_creation_projects:
        project_links = format_html_join(
            ", ",
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            ((url, name) for name, url in successful_creation_projects),
        )
        modeladmin.message_user(
            request,
            message=format_html("({}) GitHub Projects Created: {}", len(successful_creation_projects), project_links),
            level=messages.INFO,
        )


create_github_organizational_project_action.short_description = _("Create Github Organizational Project(s) for selected")  # noqa: E305


def create_github_repository_milestones_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:
    """
    Admin Action command to create a github repository milestones for ALL
    repositories linked to the selected KippoProject(s).
    """
    for kippo_project in queryset:
        milestones = kippo_project.active_milestones()
        for milestone in milestones:
            try:
                created_octocat_milestones = milestone.update_github_milestones(request.user)
                for _, created_octocat_milestone in created_octocat_milestones:
                    modeladmin.message_user(
                        request,
                        message=f"({kippo_project.name}) {created_octocat_milestone.repository.name} created milestone: "
                        f"{milestone.title} ({milestone.start_date} - {milestone.target_date})",
                        level=messages.INFO,
                    )
            except GithubMilestoneAlreadyExistsError as e:
                modeladmin.message_user(
                    request,
                    message=f"({kippo_project.name}) Failed to create milestone for related repository(ies): {e.args}",
                    level=messages.ERROR,
                )


create_github_repository_milestones_action.short_description = _("Create related Github Repository Milestone(s) for selected")  # noqa: E305


def collect_project_github_repositories_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:
    """
    Admin action to collect the github repositories for selected KippoProjects
    Calls `()` which also updates issues on collection
    """
    # get request user organization
    organization, user_organizations = get_user_session_organization(request)

    # collect project github html_urls to filter for the collect_github_project_issues functoin
    github_project_html_urls_to_update = []
    for kippoproject in queryset.filter(organization__in=user_organizations):  # apply filter to only access user accessible orgs
        logger.debug(f"adding project: {kippoproject}")
        github_project_html_urls_to_update.append(kippoproject.github_project_html_url)

    collect_github_project_issues(1, kippo_organization_id=str(organization.id), github_project_html_urls=github_project_html_urls_to_update)
    modeladmin.message_user(
        request,
        message=f"({len(github_project_html_urls_to_update)}) KippoProjects updated from GitHub Organizational Projects",
        level=messages.INFO,
    )


collect_project_github_repositories_action.short_description = _("Collect Project Repositories")  # noqa: E305


class CloseProjectActionForm(forms.Form):
    FOLLOW_UP_CHOICES = (
        (CONTINUATION_LEAD_SOURCE_VALUE, _("継続")),
        (CLOSE_PROJECT_NO_CONTINUATION_VALUE, _("継続なし")),
    )

    follow_up = forms.ChoiceField(
        label=_("継続"),
        widget=forms.Select,
        choices=FOLLOW_UP_CHOICES,
        initial=CLOSE_PROJECT_NO_CONTINUATION_VALUE,
    )
    close_comment = forms.CharField(
        label=_("Close Comment"),
        widget=forms.Textarea(attrs={"rows": 4, "cols": 60}),
        required=False,
    )

    def clean(self):
        cleaned_data = super().clean()
        follow_up = cleaned_data.get("follow_up")
        close_comment = cleaned_data.get("close_comment", "").strip()
        if follow_up == CLOSE_PROJECT_NO_CONTINUATION_VALUE and not close_comment:
            raise ValidationError({"close_comment": _("Close Comment is required when 継続なし is selected.")})
        return cleaned_data


def _next_continuation_project_name(name: str) -> str:
    match = re.match(r"(.*) Phase (\d+)$", name)
    if match:
        return f"{match.group(1)} Phase {int(match.group(2)) + 1}"
    return f"{name} Phase 2"


def _start_of_next_month(today: datetime.date) -> datetime.date:
    return (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)


def _build_continuation_prefill_params(project: KippoProject) -> dict[str, str]:
    today = timezone.now().date()
    # The follow-up is a 継続 (continuation) project: stamp lead_source, link the parent, and inherit
    # the parent's sourcing/context. category is left to the add form's default (その他).
    params = {
        "lead_source": CONTINUATION_LEAD_SOURCE_VALUE,
        "parent_project": str(project.id),
        "organization": str(project.organization_id),
        CONTINUATION_SOURCE_PARAM: CONTINUATION_SOURCE_VALUE,
        "name": _next_continuation_project_name(project.name),
        "start_date": _start_of_next_month(today).isoformat(),
        "slack_channel_name": project.slack_channel_name,
        "slack_notification_channel_name": project.slack_notification_channel_name,
        "document_folder_url": project.document_folder_url,
        "github_project_html_url": project.github_project_html_url,
        "github_project_api_nodeid": project.github_project_api_nodeid,
        "docbase_tag": ",".join(project.docbase_tag),  # type: ignore[arg-type]
    }
    if project.columnset_id:
        params["columnset"] = str(project.columnset_id)
    return {k: v for k, v in params.items() if v}


def _close_origin_opts(modeladmin: admin.ModelAdmin, request: DjangoRequest) -> "models.options.Options":
    """Meta options of the admin the close action was started from.

    The confirmation form posts to the all-projects changelist, so on that POST `modeladmin` is
    always KippoProjectAdmin — the origin comes from the form's hidden CLOSE_ORIGIN_PARAM instead.
    Only registered KippoProject admins are accepted (no arbitrary reverse target); anything else
    falls back to the dispatching admin.
    """
    requested = request.POST.get(CLOSE_ORIGIN_PARAM)
    if requested:
        for model in modeladmin.admin_site._registry:
            if issubclass(model, KippoProject) and model._meta.model_name == requested:
                return model._meta
    return modeladmin.model._meta


def close_kippoproject_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet):
    """Close a KippoProject with an optional 継続 (continuation) follow-up project."""
    if queryset.count() != 1:
        modeladmin.message_user(request, _("Select exactly one project to close."), level=messages.ERROR)
        return None

    project: KippoProject = queryset.first()
    if project.is_closed:
        modeladmin.message_user(
            request,
            _("Project is already closed; re-open the project before closing again."),
            level=messages.ERROR,
        )
        return None

    # Send the user back to the changelist (or continuation add form) of the admin they started
    # from — 実行中/営業中 proxies included — instead of always the all-projects admin.
    origin_opts = _close_origin_opts(modeladmin, request)
    origin_urlname = f"admin:{origin_opts.app_label}_{origin_opts.model_name}"

    if request.POST.get("post") == "yes":
        form = CloseProjectActionForm(request.POST)
        if form.is_valid():
            follow_up = form.cleaned_data["follow_up"]
            project.close_comment = form.cleaned_data["close_comment"]
            # A closed project is delivered — stamp the 完了 phase (idempotent; save() re-derives
            # confidence only when the phase actually changes).
            project.phase = PHASE_COMPLETED
            project.is_closed = True
            project.actual_date = timezone.now().date()
            project.display_as_active = False
            project.display_in_project_report = False
            project.updated_by = request.user
            project.save()

            modeladmin.message_user(request, _("Project '%s' closed.") % project.name, level=messages.INFO)

            if follow_up == CONTINUATION_LEAD_SOURCE_VALUE:
                params = urllib.parse.urlencode(_build_continuation_prefill_params(project))
                return HttpResponseRedirect(f"{reverse(f'{origin_urlname}_add')}?{params}")
            return HttpResponseRedirect(reverse(f"{origin_urlname}_changelist"))
    else:
        form = CloseProjectActionForm()

    context = {
        **modeladmin.admin_site.each_context(request),
        "title": _("Close Project"),
        "project": project,
        "form": form,
        "action": "close_kippoproject_action",
        # origin_opts (not modeladmin's) so the breadcrumbs/Cancel link keep pointing at the
        # originating admin when the confirmation page re-renders with form errors.
        "opts": origin_opts,
        "close_origin": origin_opts.model_name,
        "no_continuation_value": CLOSE_PROJECT_NO_CONTINUATION_VALUE,
    }
    return TemplateResponse(request, "admin/projects/close_project_action.html", context)


close_kippoproject_action.short_description = _("Close Project")  # noqa: E305


def reopen_kippoproject_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet):
    """Re-open one or more closed KippoProjects and record a status comment."""
    selected = list(queryset)
    closed_projects = [p for p in selected if p.is_closed]
    skipped_count = len(selected) - len(closed_projects)
    if skipped_count:
        modeladmin.message_user(
            request,
            _("%(count)d project(s) were not closed and were skipped.") % {"count": skipped_count},
            level=messages.WARNING,
        )
    if not closed_projects:
        modeladmin.message_user(request, _("No closed projects selected to re-open."), level=messages.ERROR)
        return

    reopen_comment = _("re-opened by %(username)s") % {"username": request.user.username}
    for project in closed_projects:
        project.is_closed = False
        project.actual_date = None
        project.display_as_active = True
        project.display_in_project_report = True
        project.updated_by = request.user
        project.save()
        KippoProjectStatus.objects.create(
            project=project,
            comment=reopen_comment,
            created_by=request.user,
            updated_by=request.user,
        )
    modeladmin.message_user(
        request,
        _("Re-opened %(count)d project(s).") % {"count": len(closed_projects)},
        level=messages.INFO,
    )


reopen_kippoproject_action.short_description = _("Re-open Project(s)")  # noqa: E305


def fill_remaining_participant_months_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet) -> None:
    """kippo#75 G3: create the missing remaining-period (R1) rows for every participant of the selected projects.

    Rows use each participant's stored percentage / role; months that already have a row are never changed.
    Closed projects and projects without a remaining period are skipped.
    """
    closed_count = queryset.filter(is_closed=True).count()
    if closed_count:
        modeladmin.message_user(request, _("Skipped %(count)d closed project(s).") % {"count": closed_count}, level=messages.WARNING)
    total_created = 0
    for project in queryset.filter(is_closed=False).prefetch_related("participants__user"):
        period_error = participant_period_error(project) if project.start_date and project.target_date else None
        if period_error:
            _field, message, _code = period_error
            modeladmin.message_user(
                request, _("Project '%(name)s' skipped: %(reason)s") % {"name": project.name, "reason": message}, level=messages.WARNING
            )
            continue
        months = remaining_months(project)
        if not months:
            modeladmin.message_user(
                request,
                _("Project '%(name)s' skipped: no remaining period (start/target date missing or ended).") % {"name": project.name},
                level=messages.WARNING,
            )
            continue
        created_count = 0
        for participant in project.participants.all():
            try:
                created_rows, _skipped_months = create_remaining_assignments(participant, request.user)
            except ValidationError as exc:
                modeladmin.message_user(
                    request,
                    _("Project '%(name)s': %(user)s not filled: %(error)s")
                    % {"name": project.name, "user": participant.user.username, "error": "; ".join(exc.messages)},
                    level=messages.ERROR,
                )
                continue
            created_count += len(created_rows)
        total_created += created_count
        modeladmin.message_user(
            request,
            _("Project '%(name)s': created %(count)d row(s) for %(start)s 〜 %(end)s.")
            % {"name": project.name, "count": created_count, "start": months[0].strftime("%Y-%m"), "end": months[-1].strftime("%Y-%m")},
            level=messages.INFO,
        )
    modeladmin.message_user(request, _("Total rows created: %(count)d") % {"count": total_created}, level=messages.SUCCESS)


fill_remaining_participant_months_action.short_description = _("残り期間の月別割当を補完")  # noqa: E305


def add_calendar_links_to_slack_channels_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet):
    """Add each selected project's MTG calendar-template URL as a pinned message on its Slack conversation channel.

    Skips projects without a Slack conversation channel (or whose organization has no Slack API token)
    and reports them as errors. See kiconiaworks/kippo#13.
    """
    from ..slackcommand.managers import ProjectCalendarLinkManager

    added: list[str] = []
    updated: list[str] = []
    errors: list[str] = []
    managers: dict = {}
    for project in queryset.select_related("organization"):
        if not project.slack_channel_name:
            errors.append(_("%(name)s: no Slack conversation channel is configured.") % {"name": project.name})
            continue
        organization = project.organization
        if not organization.slack_api_token:
            errors.append(_("%(name)s: organization '%(org)s' has no Slack API token configured.") % {"name": project.name, "org": organization.name})
            continue
        manager = managers.get(organization.id)
        if manager is None:
            manager = ProjectCalendarLinkManager(organization)
            managers[organization.id] = manager
        try:
            result = manager.set_calendar_pinned_message(project)
        except SlackChannelNotFoundError:
            errors.append(
                _("%(name)s: Slack channel '%(channel)s' was not found in the workspace.")
                % {"name": project.name, "channel": project.slack_channel_name}
            )
        except SlackApiError as e:
            errors.append(_("%(name)s: Slack API error — %(error)s") % {"name": project.name, "error": e.response["error"]})
        else:
            (added if result == "added" else updated).append(project.name)

    if added:
        modeladmin.message_user(request, _("Added calendar link to: %s") % ", ".join(added), level=messages.INFO)
    if updated:
        modeladmin.message_user(request, _("Updated calendar link for: %s") % ", ".join(updated), level=messages.INFO)
    for error in errors:
        modeladmin.message_user(request, error, level=messages.ERROR)


add_calendar_links_to_slack_channels_action.short_description = _("Add MTG calendar link to Slack channel")  # noqa: E305


def request_employee_survey_action(modeladmin: admin.ModelAdmin, request: DjangoRequest, queryset: models.QuerySet):  # noqa: E305
    """Ask each selected project's members to fill in the 振り返り従業員アンケート, via the organization's Slack channel.

    Mentions the members who logged 週間稼働 on the project and have not responded yet, and links to the
    survey form with the project preselected. Projects whose organization has no kippo Slack channel (or
    no Slack API token) are reported as errors.
    """
    from ..services.employee_survey_request import ProjectEmployeeSurveyRequestManager

    requested: list[str] = []
    nobody_pending: list[str] = []
    errors: list[str] = []
    managers: dict = {}
    for project in queryset.select_related("organization"):
        organization = project.organization
        if not organization.slack_channel_name:
            errors.append(
                _("%(name)s: organization '%(org)s' has no kippo Slack channel configured.") % {"name": project.name, "org": organization.name}
            )
            continue
        if not organization.slack_api_token:
            errors.append(_("%(name)s: organization '%(org)s' has no Slack API token configured.") % {"name": project.name, "org": organization.name})
            continue
        manager = managers.get(organization.id)
        if manager is None:
            manager = ProjectEmployeeSurveyRequestManager(organization)
            managers[organization.id] = manager
        try:
            users = manager.post(project)
        except SlackApiError as e:
            errors.append(_("%(name)s: Slack API error — %(error)s") % {"name": project.name, "error": e.response["error"]})
        else:
            if users:
                requested.append(_("%(name)s (%(count)d)") % {"name": project.name, "count": len(users)})
            else:
                nobody_pending.append(project.name)

    if requested:
        modeladmin.message_user(request, _("Requested employee survey for: %s") % ", ".join(requested), level=messages.INFO)
    if nobody_pending:
        modeladmin.message_user(
            request,
            _("No members to request (no logged 週間稼働, or everyone has responded): %s") % ", ".join(nobody_pending),
            level=messages.WARNING,
        )
    for error in errors:
        modeladmin.message_user(request, error, level=messages.ERROR)


request_employee_survey_action.short_description = _("Request employee survey (Slack)")  # noqa: E305
