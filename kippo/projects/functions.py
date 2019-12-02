import logging
import datetime
from urllib.parse import urlsplit
from typing import List, Generator, Optional, Tuple

from django.http import HttpRequest

from ghorgs.managers import GithubOrganizationManager

from accounts.models import KippoOrganization, KippoUser
from tasks.models import KippoTaskStatus

from .models import KippoProject


logger = logging.getLogger(__name__)

TUESDAY_WEEKDAY = 2


def get_user_session_organization(request: HttpRequest) -> Tuple[KippoOrganization, List[KippoOrganization]]:
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


def collect_existing_github_projects(organization: KippoOrganization, as_user: KippoUser) -> List[KippoProject]:
    """Collect existing github organizational projects for a configured KippoOrganization"""

    manager = GithubOrganizationManager(organization=organization.github_organization_name,
                                        token=organization.githubaccesstoken.token)

    # get existing html_urls
    existing_html_urls = KippoProject.objects.filter(
        organization=organization,
        github_project_html_url__isnull=False
    ).values_list('github_project_html_url', flat=True)

    added_projects = []
    project_html_path_expected_path_component_count = 2
    for project in manager.projects():
        parsed_html_url = urlsplit(project.html_url)
        path_components = [c for c in parsed_html_url.path.split('/') if c]
        if len(path_components) == project_html_path_expected_path_component_count:
            if project.html_url not in existing_html_urls:
                # create related KippoProject
                kippo_project = KippoProject(
                    created_by=as_user,
                    updated_by=as_user,
                    organization=organization,
                    name=project.name,
                    columnset=organization.default_columnset,
                    github_project_html_url=project.html_url,
                )
                kippo_project.save()
                added_projects.append(kippo_project)
                logger.info(f'(collect_existing_github_projects) Created KippoProject: {project.name} {project.html_url}')
            else:
                logger.debug(f'(collect_existing_github_projects) Already Exists SKIPPING: {project.name}  {project.html_url}')
        else:
            logger.error(f'invalid path({parsed_html_url.path}), no KippoProject created: {project.name}  {project.html_url}')
    return added_projects


def get_kippoproject_taskstatus_csv_rows(kippo_project: KippoProject, with_headers: bool = True, status_effort_date: Optional[datetime.date] = None) -> Generator:
    """
    Generate the current taskstaus CSV lines for a given KippoProject
    """
    headers = (
        'kippo_task_id',
        'github_issue_html_url',
        'category',
        'effort_date',
        'state',
        'estimate_days',
        'assignee_github_login',
        'latest_comment'
    )
    if with_headers:
        yield headers

    if not status_effort_date:
        latest_kippotaskstatus = KippoTaskStatus.objects.filter(task__project=kippo_project).latest('created_datetime')
        status_effort_date = latest_kippotaskstatus.effort_date

    for taskstatus in KippoTaskStatus.objects.filter(
            task__project=kippo_project,
            effort_date=status_effort_date).order_by('state', 'task__category', 'task__github_issue_html_url'):
        row = (
            taskstatus.task.id,
            taskstatus.task.github_issue_html_url,
            taskstatus.task.category,
            taskstatus.effort_date,
            taskstatus.state,
            taskstatus.estimate_days,
            taskstatus.task.assignee.github_login,
            taskstatus.comment
        )
        yield row
