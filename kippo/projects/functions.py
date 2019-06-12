import logging
from typing import List

from ghorgs.managers import GithubOrganizationManager

from accounts.models import KippoOrganization, KippoUser
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
                github_project_url=project.html_url,
            )
            kippo_project.save()
            added_projects.append(kippo_project)
            logger.info(f'(collect_existing_github_projects) Created KippoProject: {project.name} {project.html_url}')
        else:
            logger.debug(f'(collect_existing_github_projects) Already Exists SKIPPING: {project.name}  {project.html_url}')
    return added_projects
