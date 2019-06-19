import datetime
import logging
from math import ceil
from typing import List, Union, Tuple

from django.conf import settings
from django.utils import timezone
from django.db.utils import IntegrityError

from ghorgs.managers import GithubOrganizationManager
from ghorgs.wrappers import GithubOrganizationProject, GithubIssue

from accounts.exceptions import OrganizationConfigurationError
from accounts.models import KippoOrganization, KippoUser
from projects.models import ActiveKippoProject, KippoMilestone
from octocat.models import GithubRepository, GithubMilestone
from ..models import KippoTask, KippoTaskStatus
from ..functions import get_github_issue_category_label, get_github_issue_estimate_label


logger = logging.getLogger(__name__)


class KippoConfigurationError(Exception):
    pass


class OrganizationIssueProcessor:

    def __init__(self, organization: KippoOrganization, status_effort_date: datetime.date = None, github_project_urls: List[str] = None):
        self.organization = organization
        self.status_effort_date = status_effort_date
        self.manager = GithubOrganizationManager(
            organization=organization.github_organization_name,
            token=organization.githubaccesstoken.token
        )
        self.github_manager_user = KippoUser.objects.get(username=settings.GITHUB_MANAGER_USERNAME)
        self.existing_tasks_by_html_url = {
            t.github_issue_html_url: t
            for t in KippoTask.objects.filter(is_closed=False) if t.github_issue_html_url
        }
        self.existing_kippo_milestones_by_html_url = {
            m.html_url: m.milestone
            for m in GithubMilestone.objects.filter(milestone__is_completed=False)
        }

        if github_project_urls:
            logger.info(f'Using Filtered github_project_urls: {github_project_urls}')
            existing_open_projects = list(ActiveKippoProject.objects.filter(github_project_url__in=github_project_urls))
        else:
            existing_open_projects = list(ActiveKippoProject.objects.filter(github_project_url__isnull=False))
        self.existing_open_projects = existing_open_projects

        self.kippo_github_users = {u.github_login: u for u in organization.get_github_developer_kippousers()}
        if settings.UNASSIGNED_USER_GITHUB_LOGIN not in self.kippo_github_users:
            raise KippoConfigurationError(f'"{settings.UNASSIGNED_USER_GITHUB_LOGIN}" must be created as a User to manage unassigned tasks')

    def github_projects(self):
        return self.manager.projects()

    def get_existing_task_by_html_url(self, html_url) -> Union[KippoTask, None]:
        task = self.existing_tasks_by_html_url.get(html_url, None)
        return task

    def get_kippo_milestone_by_html_url(self, issue: GithubIssue, html_url: str) -> KippoMilestone:
        """Get the existing related KippoMilestone for a GithubIssues's Milestone entry, if doesn't exist create it"""
        milestone = self.existing_kippo_milestones_by_html_url.get(html_url, None)
        if not milestone:
            # collect repository
            github_repository = GithubRepository.objects.get(api_url=issue.repository_url)

            # check for KippoMilestone
            try:
                kippo_milestone = KippoMilestone.objects.get(title=milestone.title)
            except KippoMilestone.DoesNotExist:
                logger.info(f'Creating KippoMilestone for issue: {issue.html_url}')
                kippo_milestone = KippoMilestone(
                    title=milestone.title,
                    target_date=milestone.due_on,  # start date is unknown
                    description=milestone.description
                )
                kippo_milestone.save()

            # create related GithubMilestone wrapper
            github_milestone = GithubMilestone(
                milestone=kippo_milestone,
                repository=github_repository,
                api_url=milestone.url,
                html_url=milestone.html_url
            )
            github_milestone.save()

            # add newly created milestone to self.existing_kippo_milestones_by_html_url
            logger.debug(f'Adding milestone.html_url({milestone.html_url}) to self.existing_kippo_milestones_by_html: {self.existing_kippo_milestones_by_html_url}')
            self.existing_kippo_milestones_by_html_url[milestone.html_url] = kippo_milestone
            milestone = kippo_milestone
        return milestone

    def process(self, kippo_project: ActiveKippoProject, issue: GithubIssue) -> Tuple[bool, List[KippoTaskStatus], List[KippoTaskStatus]]:
        kippo_milestone = None
        if issue.milestone:
            logger.info(f'GithubMilestone.html_url: {issue.milestone.html_url}')
            kippo_milestone = self.get_kippo_milestone_by_html_url(issue, issue.milestone.html_url)

        is_new_task = False
        new_taskstatus_objects = []
        updated_taskstatus_objects = []
        # check if issue is open
        # refer to github API for available fields
        # https://developer.github.com/v3/issues/
        if issue.state == 'open':
            # add related repository as GithubRepository
            repo_api_url = issue.repository_url
            repo_html_url = issue.html_url.split('issues')[0]
            name_index = -2
            issue_repo_name = repo_html_url.rsplit('/', 2)[name_index]
            try:
                kippo_github_repository = GithubRepository.objects.get(
                    name=issue_repo_name,
                    api_url=repo_api_url,
                    html_url=repo_html_url
                )
            except GithubRepository.DoesNotExist:
                kippo_github_repository = GithubRepository(
                    organization=self.organization,
                    created_by=self.github_manager_user,
                    updated_by=self.github_manager_user,
                    name=issue_repo_name,
                    api_url=repo_api_url,
                    html_url=repo_html_url,
                    label_set=self.organization.default_labelset  # may be Null/None
                )
                kippo_github_repository.save()
                logger.info(f'>>> Created GithubRepository({kippo_project} {issue_repo_name})!')

            default_task_category = kippo_github_repository.organization.default_task_category

            # check if issue exists
            existing_task = self.get_existing_task_by_html_url(issue.html_url)

            developer_assignees = [
                issue_assignee.login
                for issue_assignee in issue.assignees
                if issue_assignee.login in self.kippo_github_users
            ]
            if not developer_assignees:
                # assign task to special 'unassigned' user if task is not assigned to anyone
                logger.warning(f'No developer_assignees identified for issue: {issue.html_url}')
                developer_assignees = [settings.UNASSIGNED_USER_GITHUB_LOGIN]

            estimate_denominator = len(developer_assignees)
            for issue_assignee in developer_assignees:
                issue_assigned_user = self.kippo_github_users.get(issue_assignee, None)
                if not issue_assigned_user:
                    logger.warning(f'Not assigned ({issue_assignee}): {issue.html_url}')
                else:
                    # only add task if issue is assigned to someone in the system!
                    if not existing_task:
                        category = get_github_issue_category_label(issue)
                        if not category:
                            category = default_task_category
                        existing_task = KippoTask(
                            created_by=self.github_manager_user,
                            updated_by=self.github_manager_user,
                            title=issue.title,
                            category=category,
                            project=kippo_project,
                            milestone=kippo_milestone,
                            assignee=issue_assigned_user,
                            github_issue_api_url=issue.url,
                            github_issue_html_url=issue.html_url,
                            description=issue.body,
                        )
                        try:
                            existing_task.save()
                        except IntegrityError:
                            logger.error(f'Duplicate task: Project({kippo_project.id}) "{issue.title}" ({issue_assigned_user}), Skipping ....')
                            continue
                        is_new_task = True
                        logger.info(f'-> Created KippoTask: {issue.title} ({issue_assigned_user.username})')
                    elif existing_task.assignee.github_login not in developer_assignees:
                        # TODO: review, should multiple KippoTask objects be created for a single Github Task?
                        logger.debug(f'Updating task.assignee: {existing_task.assignee.github_login} -> {issue_assigned_user.github_login}')
                        existing_task.assignee = issue_assigned_user
                        existing_task.save()
                    elif existing_task and not existing_task.milestone and kippo_milestone:
                        logger.info(f'--> Applying NEW milestone: {kippo_milestone.title}')
                        existing_task.milestone = kippo_milestone
                        existing_task.save()

                    # only update status if active or done (want to pick up
                    # -- this condition is only met when the task is open, closed tasks will not be updated.
                    active_task_column_names = kippo_project.columnset.get_active_column_names()
                    done_task_column_names = kippo_project.columnset.get_done_column_names()
                    task_status_updates_states = active_task_column_names + done_task_column_names
                    if issue.project_column not in task_status_updates_states:
                        logger.warning(f'Task({existing_task.title}) in non-active column({issue.project_column}), '
                                       f'KippoTaskStatus NOT created!')
                    else:
                        latest_comment = ''
                        if issue.latest_comment_body:
                            latest_comment = f'{issue.latest_comment_created_by} [ {issue.latest_comment_created_at} ] ' \
                                f'{issue.latest_comment_body}'

                        unadjusted_issue_estimate = get_github_issue_estimate_label(issue)
                        adjusted_issue_estimate = None
                        if unadjusted_issue_estimate:
                            # adjusting to take into account the number of developer_assignees working on it
                            # -- divides task load by the number of developer_assignees
                            adjusted_issue_estimate = ceil(unadjusted_issue_estimate / estimate_denominator)

                        # create or update KippoTaskStatus with updated estimate
                        status, created = KippoTaskStatus.objects.get_or_create(
                            task=existing_task,
                            effort_date=self.status_effort_date,
                            defaults={
                                'created_by': self.github_manager_user,
                                'updated_by': self.github_manager_user,
                                'state': issue.project_column,
                                'state_priority': issue.column_priority,
                                'estimate_days': adjusted_issue_estimate,
                                'effort_date': self.status_effort_date,
                                'comment': latest_comment
                            }
                        )
                        # check if title was updated, if updated, update related kippotask
                        if issue.title != existing_task.title:
                            existing_task.title = issue.title
                            existing_task.save()

                        if created:
                            new_taskstatus_objects.append(status)
                            logger.info(f'--> KippoTaskStatus Added: ({self.status_effort_date}) {issue.title}')
                        else:
                            logger.info(f'--> KippoTaskStatus Already Exists, updated: ({self.status_effort_date}) {issue.title} ')
                            updated_taskstatus_objects.append(status)
        return is_new_task, new_taskstatus_objects, updated_taskstatus_objects


def get_existing_kippo_project(github_project: GithubOrganizationProject, existing_open_projects: List[ActiveKippoProject]) -> Union[ActiveKippoProject, None]:
    """
    Retrieve the KippoProject related to the given GithubOrganizationProject
    """
    kippo_project = None
    for candidate_kippo_project in existing_open_projects:
        if candidate_kippo_project.github_project_url == github_project.html_url:
            kippo_project = candidate_kippo_project
            break

    if not kippo_project:
        logger.info(f'X -- Kippo Project Not found for: {github_project.name}')
    return kippo_project


def collect_github_project_issues(kippo_organization: KippoOrganization,
                                  status_effort_date: datetime.date = None,
                                  github_project_urls: List[str] = None) -> tuple:
    """
    1. Collect issues from attached github projects
    2. If related KippoTask does not exist, create one
    3. If KippoTask exists create KippoTaskStatus

    :param kippo_organization: KippoOrganization
    :param status_effort_date: Date to get tasks from for testing, estimation purposes
    :param github_project_urls: If only specific projects are desired, the related github_project_urls may be provided
    :return: processed_projects_count, created_task_count, created_taskstatus_count
    """
    if not status_effort_date:
        status_effort_date = timezone.now().date()

    if not kippo_organization.githubaccesstoken or not kippo_organization.githubaccesstoken.token:
        raise OrganizationConfigurationError(f'Token Not configured for: {kippo_organization.name}')

    issue_processor = OrganizationIssueProcessor(
        organization=kippo_organization,
        status_effort_date=status_effort_date,
        github_project_urls=github_project_urls
    )
    # collect project issues
    processed_projects = 0
    new_task_count = 0
    new_taskstatus_objects = []
    updated_taskstatus_objects = []
    unhandled_issues = []
    for github_project in issue_processor.github_projects():
        logger.info(f'Processing github project ({github_project.name})...')

        # get the related KippoProject
        # --- For some reason standard filtering was not working as expected, so this method is being used...
        # --- The following was only returning a single project
        # --- Project.objects.filter(is_closed=False, github_project_url__isnull=False)
        kippo_project = get_existing_kippo_project(github_project, issue_processor.existing_open_projects)
        if kippo_project:
            logger.info(f'-- KippoProject: {kippo_project.name}')
            processed_projects += 1
            logger.info('-- Processing Related Github Issues...')
            count = 0
            for count, issue in enumerate(github_project.issues(), 1):
                try:
                    is_new_task, issue_new_taskstatus_objects, issue_updated_taskstatus_objects = issue_processor.process(kippo_project, issue)
                    if is_new_task:
                        new_task_count += 1
                    new_taskstatus_objects.extend(issue_new_taskstatus_objects)
                    updated_taskstatus_objects.extend(issue_updated_taskstatus_objects)
                except ValueError as e:
                    unhandled_issues.append(
                        (issue, e.args)
                    )

            logger.info(f'>>> {kippo_project.name} - processed issues: {count}')

    return processed_projects, new_task_count, len(new_taskstatus_objects), len(updated_taskstatus_objects), unhandled_issues


def run_collect_github_project_issues(event, context):
    """
    A AWS Lambda handler function for running the collect_github_project_issues() function for each organization

    .. note::

        This function will eventually be overshadowed by github webhook integration

    :param event:
    :param context:
    :return:
    """
    for organization in KippoOrganization.objects.filter(github_organization_name__isnull=False):
        collect_github_project_issues(organization)
