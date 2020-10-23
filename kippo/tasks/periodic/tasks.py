import datetime
import logging
from math import ceil
from typing import List, Optional, Tuple, Union
from urllib.parse import urlsplit

from accounts.exceptions import OrganizationConfigurationError
from accounts.models import KippoOrganization, KippoUser
from django.conf import settings
from django.db.utils import IntegrityError
from django.utils import timezone
from ghorgs.managers import GithubOrganizationManager
from ghorgs.wrappers import GithubIssue, GithubOrganizationProject
from octocat.models import GithubMilestone, GithubRepository
from projects.models import ActiveKippoProject, CollectIssuesAction, CollectIssuesProjectResult, KippoMilestone, KippoProject
from zappa.asynchronous import task

from ..exceptions import GithubPullRequestUrl, GithubRepositoryUrlError
from ..functions import (
    build_latest_comment,
    get_github_issue_category_label,
    get_github_issue_estimate_label,
    get_github_issue_prefixed_labels,
    get_tags_from_prefixedlabels,
)
from ..models import KippoTask, KippoTaskStatus

logger = logging.getLogger(__name__)


class KippoConfigurationError(Exception):
    pass


class OrganizationIssueProcessor:
    def __init__(self, organization: KippoOrganization, status_effort_date: datetime.date = None, github_project_html_urls: List[str] = None):
        self.organization = organization
        self.status_effort_date = status_effort_date
        self.manager = GithubOrganizationManager(organization=organization.github_organization_name, token=organization.githubaccesstoken.token)
        self.github_manager_user = KippoUser.objects.get(username=settings.GITHUB_MANAGER_USERNAME)
        self.unassigned_user = self.organization.get_unassigned_kippouser()
        if not self.unassigned_user:
            raise KippoConfigurationError(
                f'Username starting with "{settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX}" required to manage unassigned tasks'
            )

        self.existing_tasks_by_html_url = {
            t.github_issue_html_url: t
            for t in KippoTask.objects.filter(is_closed=False).exclude(assignee=self.unassigned_user)
            if t.github_issue_html_url
        }

        # update existing_tasks_by_html_url for unassigned users
        unassigned_taskids_to_close = []
        for unassigned_task in KippoTask.objects.filter(is_closed=False, assignee=self.unassigned_user):
            if unassigned_task.github_issue_html_url and unassigned_task.github_issue_html_url not in self.existing_tasks_by_html_url:
                self.existing_tasks_by_html_url[unassigned_task.github_issue_html_url] = unassigned_task
            else:
                # close the task assigned to an unassigned user (that means it's now assigned as expecte)
                unassigned_taskids_to_close.append(unassigned_task.id)
        if unassigned_taskids_to_close:
            logger.info(f"closing unassigned KippoTask(s), unassigned_taskids_to_close={unassigned_taskids_to_close}")
            KippoTask.objects.filter(id__in=unassigned_taskids_to_close).update(is_closed=True)

        self.existing_kippo_milestones_by_html_url = {m.html_url: m.milestone for m in GithubMilestone.objects.filter(milestone__is_completed=False)}

        if github_project_html_urls:
            logger.info(f"Using Filtered github_project_html_urls: {github_project_html_urls}")
            existing_open_projects = list(ActiveKippoProject.objects.filter(github_project_html_url__in=github_project_html_urls))
        else:
            existing_open_projects = list(ActiveKippoProject.objects.filter(github_project_html_url__isnull=False))
        self.existing_open_projects = existing_open_projects

        self.kippo_github_users = {u.github_login: u for u in organization.get_github_developer_kippousers()}

    def github_projects(self):
        return self.manager.projects()

    def get_existing_task_by_html_url(self, html_url) -> Union[KippoTask, None]:
        task = self.existing_tasks_by_html_url.get(html_url, None)
        return task

    def get_kippo_milestone_by_html_url(self, kippo_project: KippoProject, issue: GithubIssue, html_url: str) -> KippoMilestone:
        """Get the existing related KippoMilestone for a GithubIssues's Milestone entry, if doesn't exist create it"""
        assert hasattr(issue, "milestone")
        milestone = self.existing_kippo_milestones_by_html_url.get(html_url, None)
        if not milestone:
            # collect repository
            try:
                github_repository = GithubRepository.objects.get(api_url=issue.repository_url)
            except GithubRepository.DoesNotExist:
                logger.error(f"GithubRepository.DoesNotExist: {issue.repository_url}")
                raise

            # check for KippoMilestone
            milestone_title = issue.milestone.title
            try:
                kippo_milestone = KippoMilestone.objects.get(title=milestone_title, project=kippo_project)
            except KippoMilestone.DoesNotExist:
                logger.info(f"Creating KippoMilestone for issue: {issue.html_url}")
                dueon_date = datetime.datetime.fromisoformat(issue.milestone.due_on.replace("Z", "+00:00")).date()
                kippo_milestone = KippoMilestone(
                    title=milestone_title,
                    project=kippo_project,
                    target_date=dueon_date,  # start date is unknown
                    number=issue.milestone.number,
                    description=issue.milestone.description,
                    created_by=self.github_manager_user,
                    updated_by=self.github_manager_user,
                )
                kippo_milestone.save()

            # create related GithubMilestone wrapper
            logger.info(f"Creating GithubMilestone for issue: {issue.html_url}")
            github_milestone = GithubMilestone(
                milestone=kippo_milestone,
                repository=github_repository,
                number=issue.milestone.number,
                api_url=issue.milestone.url,
                html_url=issue.milestone.html_url,
                created_by=self.github_manager_user,
                updated_by=self.github_manager_user,
            )
            github_milestone.save()

            # add newly created milestone to self.existing_kippo_milestones_by_html_url
            logger.debug(
                f"Adding milestone.html_url({github_milestone.html_url}) to self.existing_kippo_milestones_by_html: {self.existing_kippo_milestones_by_html_url}"
            )
            self.existing_kippo_milestones_by_html_url[github_milestone.html_url] = kippo_milestone
            milestone = kippo_milestone
        return milestone

    def get_githubrepository(self, repo_name: str, api_url: str, html_url: str) -> GithubRepository:
        """Get the existing GithubRepository or create a new one"""
        # normalize urls
        if api_url.endswith("/"):
            api_url = api_url[:-1]
        if html_url.endswith("/"):
            html_url = html_url[:-1]
        try:
            # using '__startswith' to assure match in cases where an *older* url as added with an ending '/'.
            logger.debug(f"retrieving repo_name={repo_name}, api_url={api_url}, html_url={html_url}")
            kippo_github_repository = GithubRepository.objects.get(name=repo_name, api_url__startswith=api_url, html_url__startswith=html_url)
        except GithubRepository.DoesNotExist:
            logger.warning(f"GithubRepository.DoesNotExist: name={repo_name}, api_url={api_url}, html_url={html_url}")
            html_path_expected_path_component_count = 2
            parsed_html_url = urlsplit(html_url)
            path_components = [c for c in parsed_html_url.path.split("/") if c]
            url_type = path_components[-2]
            if len(path_components) == html_path_expected_path_component_count:
                kippo_github_repository = GithubRepository(
                    organization=self.organization,
                    created_by=self.github_manager_user,
                    updated_by=self.github_manager_user,
                    name=repo_name,
                    api_url=api_url,
                    html_url=html_url,
                    label_set=self.organization.default_labelset,  # may be Null/None
                )
                kippo_github_repository.save()
                logger.info(f">>> Created GithubRepository: repo_name={repo_name}, api_url={api_url}, html_url={html_url}")
            elif url_type == "pull":
                message = f"PullRequest detected, ignoring: {html_url}"
                raise GithubPullRequestUrl(message)
            else:
                message = f"XXX Invalid html_url for GithubRepository, SKIPPING: {html_url}"
                logger.error(message)
                raise GithubRepositoryUrlError(message)
        return kippo_github_repository

    def process(self, kippo_project: ActiveKippoProject, issue: GithubIssue) -> Tuple[bool, List[KippoTaskStatus], List[KippoTaskStatus]]:
        kippo_milestone = None
        if issue.milestone:
            if isinstance(issue.milestone, dict):
                milestone_html_url = issue.milestone.get("html_url", None)
            else:
                milestone_html_url = issue.milestone.html_url
            logger.info(f"GithubMilestone.html_url: {milestone_html_url}")
            kippo_milestone = self.get_kippo_milestone_by_html_url(kippo_project, issue, milestone_html_url)

        is_new_task = False
        new_taskstatus_objects = []
        updated_taskstatus_objects = []

        # add related repository as GithubRepository
        repo_api_url = issue.repository_url
        repo_html_url = issue.html_url.split("issues")[0]
        name_index = -2
        issue_repo_name = repo_html_url.rsplit("/", 2)[name_index]
        kippo_github_repository = self.get_githubrepository(issue_repo_name, repo_api_url, repo_html_url)
        default_task_category = kippo_github_repository.organization.default_task_category

        # check if issue exists
        logger.debug(f"html_url: {issue.html_url}")
        existing_task = self.get_existing_task_by_html_url(issue.html_url)
        logger.debug(f"existing task: {existing_task} {issue.html_url}")  # TODO: review why duplicate task error is occurring

        developer_assignees = [issue_assignee.login for issue_assignee in issue.assignees if issue_assignee.login in self.kippo_github_users]
        if not developer_assignees:
            # assign task to special 'unassigned' user if task is not assigned to anyone
            logger.warning(f"No developer_assignees identified for issue: {issue.html_url}")
            developer_assignees = [self.unassigned_user.github_login]

        estimate_denominator = len(developer_assignees)
        for issue_assignee in developer_assignees:
            issue_assigned_user = self.kippo_github_users.get(issue_assignee, None)
            if not issue_assigned_user:
                logger.warning(f"Not assigned ({issue_assignee}): {issue.html_url}")
            else:
                # only add task if issue is assigned to someone in the system!
                category = get_github_issue_category_label(issue)
                if not category:
                    category = default_task_category

                if not existing_task:
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
                    logger.info(f"-> Created KippoTask: {issue.title} ({issue_assigned_user.username})")
                elif not existing_task.assignee or existing_task.assignee.github_login not in developer_assignees:
                    # TODO: review, should multiple KippoTask objects be created for a single Github Task?
                    logger.debug(f"Updating task.assignee ({existing_task.assignee}) -> {issue_assigned_user.github_login}")
                    existing_task.assignee = issue_assigned_user
                    existing_task.save()
                elif existing_task and not existing_task.milestone and kippo_milestone:
                    logger.info(f"--> Applying NEW milestone: {kippo_milestone.title}")
                    existing_task.milestone = kippo_milestone
                    existing_task.save()

                existing_changed = False
                if issue.title != existing_task.title or issue.body != existing_task.description:
                    logger.debug("Updating KippoTask.(title|description)")
                    existing_task.title = issue.title
                    existing_task.description = issue.body
                    existing_changed = True

                latest_comment = build_latest_comment(issue)

                unadjusted_issue_estimate = get_github_issue_estimate_label(issue)
                adjusted_issue_estimate = None
                if unadjusted_issue_estimate:
                    # adjusting to take into account the number of developer_assignees working on it
                    # -- divides task load by the number of developer_assignees
                    adjusted_issue_estimate = ceil(unadjusted_issue_estimate / estimate_denominator)

                prefixed_labels = get_github_issue_prefixed_labels(issue)
                tags = get_tags_from_prefixedlabels(prefixed_labels)

                # set task state (used to determine if a task is "active" or not)
                # -- When a task is "active" the estimate is included in the resulting schedule projection
                default_column = kippo_project.default_column_name
                # get latest status, and get the latest status
                latest_kippotaskstatus = existing_task.latest_kippotaskstatus()
                if latest_kippotaskstatus and latest_kippotaskstatus.state:
                    default_column = latest_kippotaskstatus.state

                task_state = issue.project_column if issue.project_column else default_column
                logger.debug(f"KippoTask({existing_task.github_issue_html_url}) task_state: {task_state}")

                # check if title was updated, if updated, update related kippotask
                if issue.title != existing_task.title:
                    existing_task.title = issue.title
                    existing_changed = True
                if issue.is_closed != existing_task.is_closed:
                    existing_task.is_closed = issue.is_closed
                    existing_changed = True
                if existing_task.category != category:
                    existing_task.category = category
                    existing_changed = True
                if existing_changed:
                    existing_task.save()

                # create or update KippoTaskStatus with updated estimate
                status_values = {
                    "created_by": self.github_manager_user,
                    "updated_by": self.github_manager_user,
                    "state": task_state,
                    "state_priority": issue.column_priority,
                    "estimate_days": adjusted_issue_estimate,
                    "effort_date": self.status_effort_date,
                    "tags": tags,
                    "comment": latest_comment,
                }
                status, created = KippoTaskStatus.objects.get_or_create(
                    task=existing_task, effort_date=self.status_effort_date, defaults=status_values
                )

                if created:
                    new_taskstatus_objects.append(status)
                    logger.info(f"--> KippoTaskStatus Added: ({self.status_effort_date}) {issue.title}")
                else:
                    # for updated status overwrite previous values
                    if any(getattr(status, fieldname) != fieldvalue for fieldname, fieldvalue in status_values.items()):
                        logger.debug(f"Updating related {status} ...")
                        # set values
                        for fieldname, fieldvalue in status_values.items():
                            setattr(status, fieldname, fieldvalue)
                        status.save()

                    logger.info(f"--> KippoTaskStatus Already Exists, updated: ({self.status_effort_date}) {issue.title} ")
                    updated_taskstatus_objects.append(status)

        return is_new_task, new_taskstatus_objects, updated_taskstatus_objects


def get_existing_kippo_project(
    github_project: GithubOrganizationProject, existing_open_projects: List[ActiveKippoProject]
) -> Union[ActiveKippoProject, None]:
    """
    Retrieve the KippoProject related to the given GithubOrganizationProject
    """
    kippo_project = None
    for candidate_kippo_project in existing_open_projects:
        if candidate_kippo_project.github_project_html_url == github_project.html_url:
            kippo_project = candidate_kippo_project
            break

    if not kippo_project:
        logger.info(f"X -- Kippo Project Not found for: {github_project.name}")
    return kippo_project


@task
def collect_github_project_issues(
    action_tracker_id: int, kippo_organization_id: str, status_effort_date_iso8601: Optional[str] = None, github_project_html_urls: List[str] = None
) -> None:
    """
    1. Collect issues from attached github projects
    2. If related KippoTask does not exist, create one
    3. If KippoTask exists create KippoTaskStatus

    :param action_tracker_id: specific caller defined id to clearly identify the action value stored in the relatedd CollectIssuesProjectResult.action_id
    :param kippo_organization_id: KippoOrganization ID
    :param status_effort_date_iso8601: Date to get tasks from for testing, estimation purposes
    :param github_project_html_urls: If only specific projects are desired, the related github_project_html_urls may be provided
    :return: processed_projects_count, created_task_count, created_taskstatus_count
    """
    assert action_tracker_id
    kippo_organization = KippoOrganization.objects.get(id=kippo_organization_id)
    if not status_effort_date_iso8601:
        status_effort_date = timezone.now().date()
    else:
        status_effort_date = datetime.datetime.fromisoformat(status_effort_date_iso8601).date()

    if not kippo_organization.githubaccesstoken or not kippo_organization.githubaccesstoken.token:
        raise OrganizationConfigurationError(f"Token Not configured for: {kippo_organization.name}")

    issue_processor = OrganizationIssueProcessor(
        organization=kippo_organization, status_effort_date=status_effort_date, github_project_html_urls=github_project_html_urls
    )
    # collect project issues
    for github_project in issue_processor.github_projects():
        logger.info(f"Processing github project ({github_project.name})...")

        # get the related KippoProject
        # --- For some reason standard filtering was not working as expected, so this method is being used...
        # --- The following was only returning a single project
        # --- Project.objects.filter(is_closed=False, github_project_html_url__isnull=False)
        kippo_project = get_existing_kippo_project(github_project, issue_processor.existing_open_projects)
        if kippo_project:
            unhandled_issues = []
            result = CollectIssuesProjectResult(action_id=action_tracker_id, project=kippo_project, unhandled_issues=[])
            result.save()
            logger.info(f"-- Processing {kippo_project.name} Related Github Issues...")
            count = 0
            for count, issue in enumerate(github_project.issues(), 1):
                # only update status if active or done (want to pick up
                # -- this condition is only met when the task is open, closed tasks will not be updated.
                try:
                    is_new_task, issue_new_taskstatus_objects, issue_updated_taskstatus_objects = issue_processor.process(kippo_project, issue)
                    if is_new_task:
                        result.new_task_count += 1
                    result.new_taskstatus_count += len(issue_new_taskstatus_objects)
                    result.updated_taskstatus_count += len(issue_updated_taskstatus_objects)
                except ValueError as e:
                    unhandled_issues.append({"issue.id": issue.id, "valueerror.args": e.args})
            result.unhandled_issues = unhandled_issues
            logger.info(f">>> {kippo_project.name} - processed issues: {count}")
            msg = (
                f"Updated [{kippo_organization_id}] Project({kippo_project.id})\n"
                f"New KippoTasks: {result.new_task_count}\n"
                f"New KippoTaskStatus: {result.new_taskstatus_count}\n"
                f"Updated KippoTaskStatus: {result.updated_taskstatus_count}"
            )
            logger.info(msg)
            result.state = "complete"
            result.save()
        else:
            logger.warning(f"No KippoProject found for GithubProject: {github_project}")


def run_collect_github_project_issues(event, context):
    """
    A AWS Lambda handler function for running the collect_github_project_issues() function for each organization

    .. note::

        This function will eventually be overshadowed by github webhook integration

    :param event:
    :param context:
    :return:
    """
    github_manager = KippoUser.objects.get(username=settings.GITHUB_MANAGER_USERNAME)
    for organization in KippoOrganization.objects.filter(github_organization_name__isnull=False):
        action_tracker = CollectIssuesAction(organization=organization, created_by=github_manager, updated_by=github_manager)
        action_tracker.save()
        collect_github_project_issues(action_tracker.id, str(organization.id))
