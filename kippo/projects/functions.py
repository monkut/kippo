import logging
import datetime
from typing import List, Generator, Optional

from ghorgs.managers import GithubOrganizationManager

from accounts.models import KippoOrganization, KippoUser
from tasks.models import KippoTaskStatus
from .models import KippoProject


logger = logging.getLogger(__name__)

TUESDAY_WEEKDAY = 2


def collect_existing_github_projects(organization: KippoOrganization, as_user: KippoUser) -> List[KippoProject]:
    """Collect existing github organizational projects for a configured KippoOrganization"""

    manager = GithubOrganizationManager(organization=organization.github_organization_name,
                                        token=organization.githubaccesstoken.token)

    # get existing html_urls
    existing_html_urls = KippoProject.objects.filter(
        organization=organization,
        github_project_url__isnull=False
    ).values_list('github_project_url', flat=True)

    added_projects = []
    for project in manager.projects():
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
