import copy
import json
import logging
import os
from collections import Counter
from distutils.util import strtobool
from math import ceil
from typing import Dict, Generator, List, Optional
from urllib.parse import unquote_plus

from accounts.models import KippoOrganization, KippoUser
from django.conf import settings
from django.utils import timezone
from ghorgs.managers import GithubOrganizationManager
from ghorgs.wrappers import GithubIssue
from projects.models import KippoProject
from tasks.exceptions import GithubRepositoryUrlError, ProjectNotFoundError
from tasks.models import KippoTask, KippoTaskStatus
from tasks.periodic.tasks import OrganizationIssueProcessor
from zappa.asynchronous import task as zappa_task

from .models import GithubWebhookEvent

logger = logging.getLogger(__name__)

KIPPO_TESTING = strtobool(os.getenv("KIPPO_TESTING", "False"))
THREE_MINUTES = 3 * 60


class GithubIssuePrefixedLabel:
    def __init__(self, label: object, prefix_delim: str = ":"):
        self.label = label
        self.prefix_delim = prefix_delim

        # https://developer.github.com/v3/issues/labels/#get-a-single-label
        label_attributes = ("id", "node_id", "url", "name", "color", "default")
        for attrname in label_attributes:
            attrvalue = getattr(label, attrname)
            setattr(self, attrname, attrvalue)

    @property
    def prefix(self):
        return self.name.split(self.prefix_delim)[0]

    @property
    def value(self):
        return self.name.split(self.prefix_delim)[-1]


def get_github_issue_estimate_label(
    issue: GithubIssue, prefix: str = settings.DEFAULT_GITHUB_ISSUE_LABEL_ESTIMATE_PREFIX, day_workhours: int = settings.DAY_WORKHOURS
) -> int:
    """
    Parse the estimate label into an estimate value
    Estimate labels follow the scheme: {prefix}N{suffix}
    WHERE:
    - {prefix} estimate label identifier
    - N is a positive integer representing number of days
    - {suffix} one of ('d', 'day', 'days', 'h', 'hour', 'hours')
    - If multiple estimate labels are defined the larger value will be used
    - If no suffix is given, 'days' will be assumed

    .. note::

        Only integer values are supported.
        (fractional days are not represented at the moment)


    :param issue: github issue object
    :param prefix: This identifies the github issue label as being an
    :param day_workhours: Number of hours in the workday
    :return: parsed estimate result in days
    """
    estimate = None
    valid_label_suffixes = ("d", "day", "days", "h", "hour", "hours")
    for label in issue.labels:
        if label.name.startswith(prefix):
            estimate_str_value = label.name.split(prefix)[-1].strip()
            for suffix in valid_label_suffixes:
                if estimate_str_value.endswith(suffix):  # d = days, h = hours
                    estimate_str_value = estimate_str_value.split(suffix)[0]
            try:
                candidate_estimate = int(estimate_str_value)
            except ValueError:
                logger.error(f"Invalid estimate value cannot convert to int() estimate_str_value={estimate_str_value}, label.name={label.name}")

            if candidate_estimate:
                if label.name.endswith(("h", "hour", "hours")):
                    # all estimates are normalized to days
                    # if hours convert to a days
                    candidate_estimate = int(ceil(candidate_estimate / day_workhours))

                if estimate and candidate_estimate:
                    if candidate_estimate > estimate:
                        logger.warning(
                            f"multiple estimate labels found for issue({issue}), using the larger value: {estimate} -> {candidate_estimate}"
                        )
                        estimate = candidate_estimate
                else:
                    estimate = candidate_estimate

    return estimate


def build_latest_comment(issue: GithubIssue) -> str:
    latest_comment = ""
    if issue.latest_comment_body:
        latest_comment = f"{issue.latest_comment_created_by} [ {issue.latest_comment_created_at} ] " f"{issue.latest_comment_body}"
    return latest_comment


def get_github_issue_category_label(issue: GithubIssue, prefix=settings.DEFAULT_GITHUB_ISSUE_LABEL_CATEGORY_PREFIX) -> str:
    """
    Parse the category label into the category value
    Category Labels follow the scheme:
        category:CATEGORY_NAME
        WHERE:
            CATEGORY_NAME should match the VALID_TASK_CATEGORIES value in models.py
    :param issue: github issue object
    :param prefix: This identifies the github issue label as being a category
    :return: parsed category result
    """
    category = None
    for label in issue.labels:
        if label.name.startswith(prefix):
            if category:
                logger.warning(
                    f"Multiple Category labels applied on issue: "
                    f"{issue.html_url}, "
                    f"prefix={prefix}, "
                    f"category={category}, "
                    f"label.name={label.name} "
                    "-- initial category will be used!"
                )
                continue
            category = label.name.split(prefix)[-1].strip()
    return category


def get_github_issue_prefixed_labels(issue: GithubIssue, prefix_delim: str = ":") -> List[GithubIssuePrefixedLabel]:
    """Process a label in the format of a prefix/value"""
    prefixed_labels = []
    for label in issue.labels:
        prefixed_label = GithubIssuePrefixedLabel(label, prefix_delim=prefix_delim)
        prefixed_labels.append(prefixed_label)
    return prefixed_labels


def get_tags_from_prefixedlabels(prefixed_labels: List[GithubIssuePrefixedLabel]) -> List[Dict[str, str]]:
    tags = []
    for prefixed_label in prefixed_labels:
        # more than 1 label with the same prefix may exist
        tags.append({"name": prefixed_label.prefix, "value": prefixed_label.value})
    return tags


def get_repo_url_from_issuecomment_url(url: str) -> str:
    # https://api.github.com/repos/octocat/Hello-World/issues/comments/1
    if url.startswith("https://api.github.com"):
        # "https://api.github.com/repos/octocat/Hello-World/issues/comments/1"
        repo_url = url.rsplit("/", 3)[0]
    elif url.startswith("https://github.com"):
        # "https://github.com/octocat/Hello-World/issues/1347#issuecomment-1"
        repo_url = url.rsplit("/", 2)[0]
    return repo_url


def queue_incoming_project_card_event(organization: KippoOrganization, event_type: str, event: dict) -> GithubWebhookEvent:
    # NOTE: Consider moving to SQS
    # card should contain a 'content_url' representing the issue attached (if an issue card)
    # - Use the 'content_url' to retrieve the internally managed issue,
    # - find the related project and issue an update for that project
    #   (Overkill, but for now this is the cleanest way without a ghorgs re-write)
    # Accept any event (ignoring action)
    webhook_event = GithubWebhookEvent(organization=organization, event_type=event_type, event=event)
    webhook_event.save()
    logger.debug(f' -- webhookevent created: {event_type}:{event["action"]}')

    return webhook_event


@zappa_task
def process_webhookevent_ids(webhookevent_ids: List[str]) -> Counter:
    logger.info(f"Processing GithubWebhookEvent(s): {webhookevent_ids}")
    webhookevents_for_update = GithubWebhookEvent.objects.filter(id__in=webhookevent_ids)
    webhookevents = copy.copy(webhookevents_for_update)
    webhookevents_for_update.update(state="processing")

    processor = GithubWebhookProcessor()
    processed_counts = processor.process_webhook_events(webhookevents)
    return processed_counts


class GithubWebhookProcessor:
    def __init__(self):
        self.organization_issue_processors = {}
        self.github_manager_kippouser = KippoUser.objects.get(username=settings.GITHUB_MANAGER_USERNAME)

    def get_organization_issue_processor(self, organization: KippoOrganization) -> OrganizationIssueProcessor:
        org_id = organization.id
        processor = self.organization_issue_processors.get(org_id, None)
        if not processor:
            processor = OrganizationIssueProcessor(organization=organization, status_effort_date=timezone.now().date())
            self.organization_issue_processors[org_id] = processor
        return processor

    @staticmethod
    def _load_event_to_githubissue(event):
        """Convert a given Issue event to a ghorgs.wrappers.GithubIssue"""
        # clean quoted data
        unquote_keys = ("body", "title")
        for key in unquote_keys:
            if key in event["issue"]:
                event["issue"][key] = unquote_plus(event["issue"][key])
        issue_json = json.dumps(event["issue"])

        # GithubIssue.from_dict() alone does not perform nested conversion, using json
        issue = json.loads(issue_json, object_hook=GithubIssue.from_dict)
        return issue

    def _process_projectcard_event(self, webhookevent: GithubWebhookEvent) -> str:
        """
        Process the 'project_card' event and update the related KippoTaskStatus.state field
        > If KippoTaskStatus does not exist for the current date create one based on the 'latest'.
        """
        assert webhookevent.event_type == "project_card"

        # identify project, retrieve related KippoProject
        github_project_api_url = webhookevent.event["project_card"]["project_url"]
        logger.debug(f"github_project_api_url={github_project_api_url}")
        try:
            kippo_project = KippoProject.objects.get(github_project_api_url=github_project_api_url)
        except KippoProject.DoesNotExist as e:
            logger.exception(e)
            logger.error(
                f"GithubWebhookEvent({webhookevent}) related KippoProject not found: event.project_card.project_url={github_project_api_url}"
            )
            state = "error"
            return state

        if kippo_project:
            if "content_url" not in webhookevent.event["project_card"]:
                logger.warning(f'webhookevent({webhookevent.id}).event does not contain "content_url" key, IGNORE (notes not supported)!')
                state = "ignore"
            else:
                logger.info(f"processing {kippo_project} webhook event...")
                task_api_url = webhookevent.event["project_card"]["content_url"]
                github_column_id = int(webhookevent.event["project_card"]["column_id"])
                column_name = kippo_project.get_columnname_from_id(github_column_id)
                if not column_name:
                    logger.error(
                        f"column_name for column_id({github_column_id}) not in KippProject.get_columnset_id_to_name_mapping(): "
                        f"{kippo_project.get_columnset_id_to_name_mapping()}"
                    )
                    state = "error"
                else:
                    # 'column_name' is used to manage KippoTask state
                    # github_from_column_id = webhookevent.event['changes']['column_id']['from']  # ex: 4162976
                    state = "processed"
                    current_action = webhookevent.event["action"]
                    if current_action in ("created", "converted", "moved"):
                        # update task state (column) for related task
                        #
                        # Sample "project_card" (moved) event
                        # {
                        #     "action": "moved",
                        #     "changes": {
                        #         "column_id": {
                        #             "from": 4162978
                        #         }
                        #     },
                        #     "project_card": {
                        #         "url": "https://api.github.com/projects/columns/cards/24713551",
                        #         "project_url": "https://api.github.com/projects/2075296",
                        #         "column_url": "https://api.github.com/projects/columns/4162976",
                        #         "column_id": 1234567,
                        #         "id": 24711234,
                        #         "node_id": "MDAC2lByb2plY3RDYXJkMjQ3M2jINTE=",
                        #         "note": null,
                        #         "archived": false,
                        #         "creator": {
                        #             ...
                        #         },
                        #         "created_at": "2019-08-02T04:26:12Z",
                        #         "updated_at": "2019-08-02T13:21:34Z",
                        #         "content_url": "https://api.github.com/repos/myorg/myrepo/issues/175",
                        #         "after_id": null
                        #     },
                        #     "organization": {
                        #         ...
                        #     },
                        #     "sender": {
                        #         ...
                        #     }
                        # }
                        card_id = webhookevent.event["project_card"]["id"]
                        github_manager = GithubOrganizationManager(
                            organization=webhookevent.organization.github_organization_name, token=webhookevent.organization.githubaccesstoken.token
                        )
                        tasks = KippoTask.objects.filter(github_issue_api_url=task_api_url)
                        issue = github_manager.get_github_issue(api_url=task_api_url)
                        if not tasks:
                            logger.warning(f"Related KippoTask not found for: {task_api_url}")
                            # Create related KippoTask
                            # - get task info
                            logger.debug("preparing GithubOrganizationManager to retrieve GithubIssue...")

                            github_manager_user = KippoUser.objects.get(username=settings.GITHUB_MANAGER_USERNAME)
                            logger.debug(f"Retrieving issue github_manager.get_github_issue(): {task_api_url}")
                            issue = github_manager.get_github_issue(api_url=task_api_url)

                            category = get_github_issue_category_label(issue)
                            if not category:
                                category = webhookevent.organization.default_task_category

                            organization_unassigned_user = webhookevent.organization.get_unassigned_kippouser()
                            organization_developer_users = {u.github_login: u for u in webhookevent.organization.get_github_developer_kippousers()}
                            organization_kippo_github_logins = organization_developer_users.keys()
                            developer_assignees = [
                                issue_assignee.login for issue_assignee in issue.assignees if issue_assignee.login in organization_kippo_github_logins
                            ]
                            if not developer_assignees:
                                # assign task to special 'unassigned' user if task is not assigned to anyone
                                logger.warning(f"No developer_assignees identified for issue: {issue.html_url}")
                                developer_assignees = [organization_unassigned_user]
                            tasks = []
                            for issue_assignee in developer_assignees:
                                organization_user = organization_developer_users.get(issue_assignee, organization_unassigned_user)
                                logger.info(f"Creating KippoTask for user({organization_user})...")
                                task = KippoTask(
                                    created_by=github_manager_user,
                                    updated_by=github_manager_user,
                                    title=issue.title,
                                    category=category,
                                    project=kippo_project,
                                    milestone=None,
                                    assignee=organization_user,
                                    project_card_id=card_id,
                                    github_issue_api_url=task_api_url,
                                    github_issue_html_url=issue.html_url,
                                    description=issue.body,
                                )
                                task.save()
                                tasks.append(task)
                        logger.debug(f"len(tasks)={len(tasks)}")
                        prefixed_labels = get_github_issue_prefixed_labels(issue)
                        tags = get_tags_from_prefixedlabels(prefixed_labels)
                        for task in tasks:
                            # update task.project_card_id
                            if task.project_card_id != card_id:
                                # Don't expect this to happen, a project_card_ids a KippoTask *should* only belong to 1 project
                                msg = f"Current_process_ KippoTask.project_card_id({task.project_card_id}) != card_id({card_id}), updating KippoTask: {task}"
                                logger.warning(msg)
                            task.project_card_id = card_id

                            if task.project is None:
                                logger.warning(f"Updating task.project to: {kippo_project}")
                                task.project = kippo_project
                            task.save()

                            # get latest github taskstatus
                            # TODO: Update to retrieve latest github issue status

                            # create/update KippoTaskStatus
                            # KippoTask created with 'issues' event
                            # -- update 'state' info if KippoTaskStatus exists
                            try:
                                status = KippoTaskStatus.objects.filter(task=task).latest("created_datetime")
                                logger.info(f"Updating KippoTaskStatus for task({task}) ...")
                            except KippoTaskStatus.DoesNotExist:
                                logger.warning("KippoTaskStatus.DoesNotExist, status set to None (KippoTaskStatus will be newly created)")
                                status = None

                            effort_date = timezone.now().date()
                            unadjusted_issue_estimate = get_github_issue_estimate_label(issue)
                            latest_comment = build_latest_comment(issue)
                            if not status or status.effort_date != effort_date:
                                # create a new KippoTaskStatus Entry
                                logger.info(f"Creating KippoTaskStatus for task({task}) ...")
                                if not status:
                                    priority = 0  # DEFAULT
                                else:
                                    priority = status.state_priority

                                status = KippoTaskStatus(
                                    task=task,
                                    effort_date=effort_date,
                                    state_priority=priority,
                                    estimate_days=unadjusted_issue_estimate,
                                    tags=tags,
                                    comment=latest_comment,
                                    created_by=self.github_manager_kippouser,
                                    updated_by=self.github_manager_kippouser,
                                )
                            else:
                                status.estimate_days = unadjusted_issue_estimate
                                status.comment = latest_comment
                            # update column state!
                            status.state = column_name
                            status.save()
                            logger.info(f"KippoTaskStatus.state updated to: {column_name}")
            return state

    def _process_issues_event(self, webhookevent: GithubWebhookEvent) -> str:
        assert webhookevent.event_type == "issues"
        githubissue = self._load_event_to_githubissue(webhookevent.event)
        repository_api_url = githubissue.repository_url
        # get related kippo project
        # -- get project with existing task
        candidate_projects = {p.name: p for p in KippoProject.objects.filter(kippotask_project__github_issue_api_url=githubissue.url)}
        if not candidate_projects:
            logger.warning(
                f"No exact match found for github_issue_api_url={githubissue.url}, finding projects by repository_api_url={repository_api_url}"
            )
            candidate_projects = {
                p.name: p for p in KippoProject.objects.filter(kippotask_project__github_issue_api_url__startswith=repository_api_url)
            }

        logger.debug(f"len(candidate_projects)={candidate_projects}")
        if len(candidate_projects) > 1:
            logger.warning(f"More than 1 KippoProject found for Issue.repository_url={repository_api_url}: {[p for p in candidate_projects.keys()]}")
        elif len(candidate_projects) <= 0:
            raise ProjectNotFoundError(
                f"KippoProject NOT found for Issue.repository_url={repository_api_url}: {[p for p in candidate_projects.keys()]}"
            )

        # only process if KippoTask already exists for GithubIssue
        # --> Introduced to avoid issue where multiple tasks were created for all available projects
        if KippoTask.objects.filter(github_issue_html_url=githubissue.html_url).exists():
            result = "error"
            issue_processor = self.get_organization_issue_processor(webhookevent.organization)
            for project_name, project in candidate_projects.items():
                try:
                    is_new_task, new_taskstatus_entries, updated_taskstatus_entries = issue_processor.process(project, githubissue)
                    result = "processed"
                    logger.info(f"project_name={project_name}, is_new_task={is_new_task}")
                except GithubRepositoryUrlError as e:
                    logger.exception(e)
                    result = "error"
                    break
        else:
            logger.info(f"Related KippoTask does not exist ({githubissue.html_url}) ignoring.")
            result = "ignore"
        return result

    def _process_issuecomment_event(self, webhookevent: GithubWebhookEvent) -> str:
        assert webhookevent.event_type == "issue_comment"
        githubissue = self._load_event_to_githubissue(webhookevent.event)

        # populate GithubIssue.latest_comment* fields before processing
        # -- This enables kippo to process the GithubIssue through the standard processor
        comment = webhookevent.event["comment"]
        githubissue.latest_comment_body = unquote_plus(comment["body"])
        githubissue.latest_comment_created_by = comment["user"]["login"]
        githubissue.latest_comment_created_at = comment["created_at"]

        issue_api_url = comment["url"]
        issue_html_url = comment["html_url"]

        repo_api_url = get_repo_url_from_issuecomment_url(issue_api_url)
        repo_html_url = get_repo_url_from_issuecomment_url(issue_html_url)
        repo_name = repo_html_url.split("/")[-1]

        issue_processor = self.get_organization_issue_processor(webhookevent.organization)
        # creates GithubRepository for Kippo Management if it doesn't exist
        issue_processor.get_githubrepository(repo_name, api_url=repo_api_url, html_url=repo_html_url)

        # get related kippo project
        # -- NOTE: Currently a GithubIssue may only be assigned to 1 Project
        repository_api_url = githubissue.repository_url
        candidate_projects = {p.name: p for p in KippoProject.objects.filter(kippotask_project__github_issue_api_url__startswith=repository_api_url)}
        if len(candidate_projects) > 1:
            logger.debug(f"len(candidate_projects)={candidate_projects}")
            logger.warning(f"More than 1 KippoProject found for Issue.repository_url={repository_api_url}: {[p for p in candidate_projects.keys()]}")
        elif len(candidate_projects) <= 0:
            raise ProjectNotFoundError(
                f"KippoProject NOT found for Issue.repository_url={repository_api_url}: {[p for p in candidate_projects.keys()]}"
            )

        result = "error"
        for project_name, project in candidate_projects.items():
            try:
                is_new_task, new_taskstatus_entries, updated_taskstatus_entries = issue_processor.process(project, githubissue)
                result = "processed"
            except GithubRepositoryUrlError as e:
                logger.exception(e)
                result = "error"
                break

        return result

    def _get_events(self) -> Generator:
        # process event_types in the following order
        # - Make sure that issue is created and linked to the appropriate project (via project_card)
        event_types_to_process = ("project_card", "issues", "issue_comment")
        for event_type in event_types_to_process:
            unprocessed_events_for_update = GithubWebhookEvent.objects.filter(state="unprocessed", event_type=event_type).order_by("created_datetime")
            unprocessed_events = copy.copy(unprocessed_events_for_update)
            unprocessed_events_for_update.update(state="processing")
            if event_type == "issues":
                # make sure "opened" action events are processed first
                other_actions = []
                for e in unprocessed_events:
                    if e.event["action"] == "opened":
                        yield e
                    else:
                        other_actions.append(e)
                for e in other_actions:
                    yield e
            else:
                yield from unprocessed_events

    def process_webhook_events(self, webhookevents: Optional[List[GithubWebhookEvent]] = None) -> Counter:
        processed_events = Counter()

        if not webhookevents:
            unprocessed_events = self._get_events()
        else:
            unprocessed_events = webhookevents

        eventtype_method_mapping = {
            "project_card": self._process_projectcard_event,
            "issues": self._process_issues_event,
            "issue_comment": self._process_issuecomment_event,
        }

        for webhookevent in unprocessed_events:
            eventtype_processing_method = eventtype_method_mapping[webhookevent.event_type]
            try:
                result_state = eventtype_processing_method(webhookevent)
            except ProjectNotFoundError as e:
                logger.exception(e)
                logger.error(f"ProjectNotFoundError: {e.args}")
                result_state = "ignore"
                webhookevent.event["kippoerror"] = "No related project found for task!"
            logger.debug(f"result_state={result_state}")
            webhookevent.state = result_state
            webhookevent.save()
            processed_events[webhookevent.event_type] += 1
        return processed_events
