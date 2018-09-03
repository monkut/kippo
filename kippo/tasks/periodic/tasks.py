import datetime
import logging
from django.conf import settings
from django.utils import timezone
from django.db import IntegrityError
from django.apps import apps
from ghorgs.managers import GithubOrganizationManager
from accounts.exceptions import OrganizationConfigurationError
from ..models import KippoTask, KippoTaskStatus
from ..functions import get_github_issue_category_label, get_github_issue_estimate_label

if settings.TEST:
    from octocat.mocks import GithubOrganizationManagerMock as GithubOrganizationManager


# load models from other apps
KippoProject = apps.get_model(app_label='projects', model_name='KippoProject')
ActiveKippoProject = apps.get_model(app_label='projects', model_name='ActiveKippoProject')
GithubRepository = apps.get_model(app_label='octocat', model_name='GithubRepository')
PersonalHoliday = apps.get_model(app_label='accounts', model_name='PersonalHoliday')

KippoOrganization = apps.get_model(app_label='accounts', model_name='KippoOrganization')
KippoUser = apps.get_model(app_label='accounts', model_name='KippoUser')
GITHUB_MANAGER_USER = KippoUser.objects.get(username=settings.GITHUB_MANAGER_USERNAME)


logger = logging.getLogger(__name__)


class KippoConfigurationError(Exception):
    pass


def collect_github_project_issues(kippo_organization: KippoOrganization, status_effort_date: datetime.date=None) -> tuple:
    """
    1. Collect issues from attached github projects
    2. If related KippoTask does not exist, create one
    3. If KippoTask exists create KippoTaskStatus

    :param kippo_organization: KippoOrganization
    :param status_effort_date: Date to get tasks from
    :return: processed_projects_count, created_task_count, created_taskstatus_count
    """
    # TODO: support non-update of done tasks
    # get done tasks for active projects and last week task status
    # if *still* in 'done' state do not create a new KippoTaskStatus entry

    if not status_effort_date:
        status_effort_date = timezone.now().date()

    if not kippo_organization.githubaccesstoken or not kippo_organization.githubaccesstoken.token:
        raise OrganizationConfigurationError(f'Token Not configured for: {kippo_organization.name}')

    manager = GithubOrganizationManager(organization=kippo_organization.github_organization_name,
                                        token=kippo_organization.githubaccesstoken.token)
    existing_tasks_by_html_url = {t.github_issue_html_url: t for t in KippoTask.objects.filter(is_closed=False) if t.github_issue_html_url}
    existing_open_projects = list(ActiveKippoProject.objects.filter(github_project_url__isnull=False))
    github_users = {u.github_login: u for u in KippoUser.objects.filter(github_login__isnull=False)}
    if settings.UNASSIGNED_USER_GITHUB_LOGIN not in github_users:
        raise KippoConfigurationError(f'"{settings.UNASSIGNED_USER_GITHUB_LOGIN}" must be created as a User to manage unassigned tasks')

    # collect project issues
    processed_projects = 0
    new_task_count = 0
    new_taskstatus_objects = []
    updated_taskstatus_objects = []
    for github_project in manager.projects():
        logger.info('Processing github project ({})...'.format(github_project.name))

        # get the related KippoProject
        # --- For some reason standard filtering was not working as expected, so this method is being used...
        # --- The following was only returning a single project, 'Project(SB Mujin)'.
        # --- Project.objects.filter(is_closed=False, github_project_url__isnull=False)
        kippo_project = None
        for candiate_kippo_project in existing_open_projects:
            if candiate_kippo_project.github_project_url == github_project.html_url:
                kippo_project = candiate_kippo_project
                break

        if not kippo_project:
            logger.info('X -- Kippo Project Not found!')
        else:
            logger.info('-- KippoProject: {}'.format(kippo_project.name))
            processed_projects += 1
            logger.info('-- Processing Related Github Issues...')
            count = 0
            for count, issue in enumerate(github_project.issues(), 1):
                # check if issue is open
                # refer to github API for available fields
                # https://developer.github.com/v3/issues/
                if issue.state == 'open':
                    # add related repository as GithubRepository
                    repo_api_url = issue.repository_url
                    repo_html_url = issue.html_url.split('issues')[0]
                    name_index = -2
                    issue_repo_name = repo_html_url.rsplit('/', 2)[name_index]
                    kippo_github_repository, created = GithubRepository.objects.get_or_create(created_by=GITHUB_MANAGER_USER,
                                                                                              updated_by=GITHUB_MANAGER_USER,
                                                                                              project=kippo_project,
                                                                                              name=issue_repo_name,
                                                                                              api_url=repo_api_url,
                                                                                              html_url=repo_html_url)
                    if created:
                        logger.info(f'>>> Created GithubRepository({kippo_project} {issue_repo_name})!')

                    default_task_category = kippo_github_repository.project.organization.default_task_category

                    # check if issue exists
                    existing_task = existing_tasks_by_html_url.get(issue.html_url, None)

                    assignees = [issue_assignee.login for issue_assignee in issue.assignees if issue_assignee.login in github_users]
                    if not assignees:
                        # assign task to special 'unassigned' user if task is not assigned to anyone
                        assignees = [settings.UNASSIGNED_USER_GITHUB_LOGIN]

                    estimate_denominator = len(assignees)
                    for issue_assignee in assignees:
                        issue_assigned_user = github_users.get(issue_assignee, None)
                        if not issue_assigned_user:
                            logger.warning(f'Not assigned ({issue_assignee}): {issue.html_url}')
                        else:
                            # only add task if issue is assigned to someone in the system!
                            if not existing_task:
                                category = get_github_issue_category_label(issue)
                                if not category:
                                    category = default_task_category
                                existing_task = KippoTask(
                                    created_by=GITHUB_MANAGER_USER,
                                    updated_by=GITHUB_MANAGER_USER,
                                    title=issue.title,
                                    category=category,
                                    project=kippo_project,
                                    assignee=issue_assigned_user,
                                    github_issue_api_url=issue.url,
                                    github_issue_html_url=issue.html_url,
                                    description=issue.body,
                                )
                                existing_task.save()
                                new_task_count += 1
                                logger.info(f'-> Created KippoTask: {issue.title} ({issue_assigned_user.username})')
                            elif existing_task.assignee.github_login not in assignees:
                                # TODO: review, should multiple KippoTask objects be created for a single Github Task?
                                logger.debug(f'Updating task.assignee: {existing_task.assignee.github_login} -> {issue_assigned_user.github_login}')
                                existing_task.assignee = issue_assigned_user
                                existing_task.save()

                            # only update status if active or done (want to pick up
                            # -- this condition is only met when the task is open, closed tasks will not be updated.
                            active_task_column_names = kippo_project.columnset.get_active_column_names()
                            done_task_column_names = kippo_project.columnset.get_done_column_names()
                            task_status_updates_states = active_task_column_names + done_task_column_names
                            if issue.project_column in task_status_updates_states:
                                latest_comment = ''
                                if issue.latest_comment_body:
                                    latest_comment = f'{issue.latest_comment_created_by} [ {issue.latest_comment_created_at} ] {issue.latest_comment_body}'

                                unadjusted_issue_estimate = get_github_issue_estimate_label(issue)
                                adjusted_issue_estimate = None
                                if unadjusted_issue_estimate:
                                    # adjusting to take into account the number of assignees working on it
                                    # -- divides task load by the number of assignees
                                    adjusted_issue_estimate = unadjusted_issue_estimate/estimate_denominator

                                # create or update KippoTaskStatus with updated estimate
                                status, created = KippoTaskStatus.objects.get_or_create(task=existing_task,
                                                                                        effort_date=status_effort_date,
                                                                                        defaults={
                                                                                            'created_by': GITHUB_MANAGER_USER,
                                                                                            'updated_by': GITHUB_MANAGER_USER,
                                                                                            'state': issue.project_column,
                                                                                            'estimate_days': adjusted_issue_estimate,
                                                                                            'effort_date': status_effort_date,
                                                                                            'comment': latest_comment
                                                                                        })
                                if created:
                                    new_taskstatus_objects.append(status)
                                    logger.info(f'--> KippoTaskStatus Added: ({status_effort_date}) {issue.title}')
                                else:
                                    logger.info(f'--> KippoTaskStatus Already Exists, updated: ({status_effort_date}) {issue.title} ')
                                    updated_taskstatus_objects.append(status)
            logger.info(f'>>> {kippo_project.name} - processed issues: {count}')

    return processed_projects, new_task_count, len(new_taskstatus_objects), len(updated_taskstatus_objects)


def run_collect_github_project_issues(event, context):
    for organization in KippoOrganization.objects.filter(github_organization_name__isnull=False):
        collect_github_project_issues(organization)
