import logging
from typing import List, Tuple, Optional
from collections import Counter, defaultdict

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseBadRequest, HttpResponse, HttpResponseRedirect, HttpRequest
from django.utils import timezone
from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.contrib.admin.views.decorators import staff_member_required

from accounts.models import KippoOrganization
from tasks.models import KippoTask, KippoTaskStatus
from tasks.functions import prepare_project_engineering_load_plot_data
from tasks.exceptions import ProjectConfigurationError
from .charts.functions import prepare_burndown_chart_components
from .models import ActiveKippoProject, KippoProject
from .exceptions import TaskStatusError, ProjectDatesError


logger = logging.getLogger(__name__)


def project_assignee_keyfunc(task_object: KippoTask) -> tuple:
    """
    A keying function that returns the values to use for sorting

    :param task_object: KippoTask task object
    :return: (task_object.assignee.username, task_object.project.name)
    """
    username = ''
    if task_object.assignee:
        username = task_object.assignee.username

    project = ''
    if task_object.project:
        project = task_object.project.name

    return project, username


def _get_user_session_organization(request: HttpRequest) -> Tuple[KippoOrganization, List[KippoOrganization]]:
    """Retrieve the session defined user KippoOrganization"""
    # get organization defined in session
    organization_id = request.session.get('organization_id', None)
    logger.debug(f'session["organization_id"] for user({request.user.username}): {organization_id}')
    # check that user belongs to organization
    user_organizations = list(request.user.organizations)
    user_organization_ids = {str(o.id): o for o in user_organizations}
    if not user_organization_ids:
        raise ValueError(f'No OrganizationMembership for user: {request.user.username}')

    if organization_id not in user_organization_ids.keys():
        # set to user first orgA
        logger.warning(f'User({request.user.username}) invalid "organization_id" given, setting to "first".')
        organization = user_organizations[0]  # use first
        request.session['organization_id'] = str(organization_id)
    else:
        organization = user_organization_ids[organization_id]
    return organization, user_organizations


@staff_member_required
def view_inprogress_projects_overview(request: HttpRequest) -> HttpResponse:
    now = timezone.now()

    try:
        selected_organization, user_organizations = _get_user_session_organization(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e.args))

    inprogress_projects = ActiveKippoProject.objects.filter(
        start_date__lte=now,
        organization=selected_organization
    ).orderby('category')

    inprogress_category_groups = defaultdict(list)
    for inprogress_project in inprogress_projects:
        inprogress_category_groups[inprogress_project.category] = inprogress_project

    upcoming_projects = ActiveKippoProject.objects.filter(
        start_date__gt=now,
        organization=selected_organization
    ).orderby('category')
    upcoming_category_groups = defaultdict(list)
    for upcoming_project in upcoming_projects:
        upcoming_category_groups[upcoming_project.category] = upcoming_project

    context = {
        'inprogress_category_groups': inprogress_category_groups,
        'upcoming_category_groups': upcoming_category_groups,
        'selected_organization': selected_organization,
        'organizations': user_organizations,
    }
    return render(request, 'projects/view_inprogress_projects_status_overview.html', context)


def _get_active_taskstatus_from_projects(
        projects: List[KippoProject],
        max_effort_date: Optional[timezone.datetime.date] = None) -> Tuple[List[KippoTaskStatus], bool]:
    active_taskstatus = []
    has_estimates = False
    for project in projects:
        done_column_names = project.columnset.get_done_column_names()
        qs = KippoTaskStatus.objects.filter(
            task__github_issue_api_url__isnull=False,  # filter out non-linked tasks
            task__project=project
        ).exclude(
            state__in=done_column_names
        )
        if max_effort_date:
            qs = qs.filter(
                effort_date__lte=max_effort_date,
            )
        results = qs.order_by('task__github_issue_api_url', '-effort_date').distinct('task__github_issue_api_url')

        taskstatus_results = list(results)
        if any(status.estimate_days for status in taskstatus_results):
            has_estimates = True
        active_taskstatus.extend(taskstatus_results)
    return active_taskstatus, has_estimates


def _get_task_details(active_taskstatus: List[KippoTaskStatus]) -> Tuple[List[int], List[KippoTask]]:
    collected_task_ids = []
    unique_tasks = []
    for taskstatus in active_taskstatus:
        if taskstatus.task.id not in collected_task_ids:
            unique_tasks.append(taskstatus.task)
            collected_task_ids.append(taskstatus.task.id)
    return collected_task_ids, unique_tasks


@staff_member_required
def view_inprogress_projects_status(request: HttpRequest) -> HttpResponse:
    warning = None

    try:
        selected_organization, user_organizations = _get_user_session_organization(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e.args))

    slug = request.GET.get('slug', None)
    if slug:
        project = get_object_or_404(KippoProject, slug=slug, organization=selected_organization)
        projects = [project]
    else:
        projects = KippoProject.objects.filter(is_closed=False, organization=selected_organization)
    active_projects = KippoProject.objects.filter(is_closed=False, organization=selected_organization).order_by('name')

    # Collect KippoTaskStatus for projects
    active_taskstatus, has_estimates = _get_active_taskstatus_from_projects(projects)

    if not has_estimates:
        msg = f'No Estimates defined in tasks (Expect "estimate labels")'
        messages.add_message(request, messages.WARNING, msg)

    project = None
    script = None
    div = None
    latest_effort_date = None
    if slug:
        assert len(projects) == 1
        project = projects[0]
        # generate burn-down chart
        try:
            script, div = prepare_burndown_chart_components(project)
        except TaskStatusError as e:
            warning = f'Data not available for project({project.name}): {e.args}'
            messages.add_message(request, messages.WARNING, warning)
            logger.warning(warning)
        except ProjectDatesError as e:
            warning = f'start_date or target_date not set for project: {e.args}'
            messages.add_message(request, messages.WARNING, warning)
            logger.warning(warning)
    else:
        # show project schedule chart
        if not selected_organization:
            return HttpResponseBadRequest(f'KippoUser not registered with an Organization!')

        # check projects for start_date, target_date
        projects_missing_dates = KippoProject.objects.filter(Q(start_date__isnull=True) | Q(target_date__isnull=True))
        projects_missing_dates = projects_missing_dates.filter(organization=selected_organization)
        if projects_missing_dates:
            for p in projects_missing_dates:
                warning = f'Project({p.name}) start_date({p.start_date}) or target_date({p.target_date}) not defined! ' \
                          f'(Will not be displayed in chart) '
                messages.add_message(request, messages.WARNING, warning)
                logger.warning(warning)
        try:
            (script, div), latest_effort_date = prepare_project_engineering_load_plot_data(selected_organization)
            logger.debug(f'latest_effort_date: {latest_effort_date}')
        except ProjectConfigurationError as e:
            logger.warning(f'No projects with start_date or target_date defined: {e.args}')
        except ValueError as e:
            logger.error(str(e.args))
            error = f'Unable to process tasks: {e.args}'
            messages.add_message(request, messages.ERROR, error)

    # collect unique Tasks
    collected_task_ids, unique_tasks = _get_task_details(active_taskstatus)

    # get user totals
    user_effort_totals = Counter()
    for task in unique_tasks:
        if task.assignee:
            days_remaining = task.effort_days_remaining() if task.effort_days_remaining() else 0
            user_effort_totals[task.assignee.username] += days_remaining

    # sort tasks by assignee.username, project.name
    sorted_tasks = sorted(unique_tasks, key=project_assignee_keyfunc)
    context = {
        'project': project,
        'tasks': sorted_tasks,
        'user_effort_totals': dict(user_effort_totals),
        'chart_script': script,
        'chart_div': div,
        'latest_effort_date': latest_effort_date,
        'active_projects': active_projects,
        'messages': messages.get_messages(request),
        'selected_organization': selected_organization,
        'organizations': user_organizations,
    }

    return render(request, 'projects/view_inprogress_projects_status.html', context)


@staff_member_required
def set_user_session_organization(request, organization_id: str = None) -> HttpResponse:
    user_organizations = list(request.user.organizations)
    if not organization_id:
        return HttpResponseBadRequest(f'required "organization_id" not given!')
    elif not user_organizations:
        return HttpResponseBadRequest(f'user({request.user.username}) has no OrganizationMemberships!')

    elif organization_id not in [str(o.id) for o in user_organizations]:
        logger.debug(f'Invalid organization_id({organization_id}) for user({request.user.username}) using user first')
        organization_id = user_organizations[0].id

    request.session['organization_id'] = str(organization_id)
    logger.debug(f'setting session["organization_id"] for user({request.user.username}): {organization_id}')
    return HttpResponseRedirect(f'{settings.URL_PREFIX}/projects/')  # go reload the page with the set org
