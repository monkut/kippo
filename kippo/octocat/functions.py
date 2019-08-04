import os
import copy
import json
import logging
from distutils.util import strtobool
from collections import Counter

from zappa.asynchronous import task
from django.utils import timezone

from ghorgs.wrappers import GithubIssue

from projects.models import KippoProject
from tasks.models import KippoTask, KippoTaskStatus
from tasks.exceptions import ProjectNotFoundError
from tasks.periodic.tasks import collect_github_project_issues, OrganizationIssueProcessor
from accounts.models import KippoOrganization
from .models import GithubWebhookEvent


logger = logging.getLogger(__name__)

KIPPO_TESTING = strtobool(os.getenv('KIPPO_TESTING', 'False'))
THREE_MINUTES = 3 * 60


def queue_incoming_project_card_event(organization: KippoOrganization, event_type: str, event: dict) -> GithubWebhookEvent:
    # NOTE: Consider moving to SQS
    # card should contain a 'content_url' representing the issue attached (if an issue card)
    # - Use the 'content_url' to retrieve the internally managed issue,
    # - find the related project and issue an update for that project
    #   (Overkill, but for now this is the cleanest way without a ghorgs re-write)
    # Accept any event (ignoring action)
    webhook_event = GithubWebhookEvent(
        organization=organization,
        event_type=event_type,
        event=event,
    )
    webhook_event.save()
    logger.debug(f' -- webhookevent created: {event_type}:{event["action"]}')

    return webhook_event


class GithubWebhookProcessor:

    def __init__(self):
        self.organization_issue_processors = {}

    def get_organization_issue_processor(self, organization: KippoOrganization) -> OrganizationIssueProcessor:
        org_id = organization.id
        processor = self.organization_issue_processors.get(org_id, None)
        if not processor:
            processor = OrganizationIssueProcessor(
                organization=organization,
                status_effort_date=timezone.now().date()
            )
            self.organization_issue_processors[org_id] = processor
        return processor

    def _load_event_to_githubissue(self, event):
        """Convert a given Issue event to a ghorgs.wrappers.GithubIssue"""
        issue_json = json.dumps(event['issue'])
        # GithubIssue.from_dict() alone does not perform nested conversion, using json
        issue = json.loads(issue_json, object_hook=GithubIssue.from_dict)
        return issue

    def _process_projectcard_event(self, webhookevent: GithubWebhookEvent):
        """
        Process the 'project_card' event and update the related state field
        :param webhookevent:
        :return:
        """
        assert webhookevent.event_type == 'project_card'
        if 'content_url' not in webhookevent.event:
            logger.warning(f'webhookevent({webhookevent.id}).event does not contain "content_url" key, SKIPPING!')
        else:
            task_api_url = webhookevent.event['project_card']['content_url']
            github_project_api_url = webhookevent.event['project_card']['project_url']
            github_column_id = webhookevent.event['project_card']['column_id']
            github_from_column_id = webhookevent.event['changes']['column_id']['from']  # ex: 4162976
            state = 'processed'
            if webhookevent.event['action'] == 'moved':
                # update task state (column) for related task
                try:
                    task = KippoTask.objects.get(github_issue_api_url=task_api_url)
                except KippoTask.DoesNotExist as e:
                    logger.exception(e)
                    state = 'error'
                    msg = f'KippoTask.github_issue_api_url={task_api_url} DoesNotExist, not updated on webhookevent "move" action'
                    logger.error(msg)
                    webhookevent.event['kippoerror'] = msg
                    return 'error'
                # TODO: add support for column_id to column_name
                # create/update KippoTaskStatus

            elif webhookevent.event['action'] == 'converted':
                # (NEW) Task create new kippo task in related project
                try:
                    project = KippoProject.objects.get(github_project_api_url=github_project_api_url)
                except KippoProject.DoesNotExist:
                    state = 'error'
                    msg = f'KippoProject.github_project_api_url={github_project_api_url} DoesNotExist, KippoTask *NOT CREATED*!'
                    logger.error(msg)
                    webhookevent.event['kippoerror'] = msg

                # KippoTask created with 'issues' event
                # -- update 'state' info if KippoTaskStatus exists
                try:
                    existing_kippotaskstatus =KippoTaskStatus.objects.filter(kippotask_set__github_issue_api_url=task_api_url).latest('created_datetime')
                except KippoTaskStatus.DoesNotExist as e:
                    logger.exception(e)
                    state = 'error'
                    msg = f'KippoTaskStatus.DoesNotExist, "state" not updated!'
                    logger.error(msg)
                    webhookevent.event['kippoerror'] = msg
                    return 'errors'
                # TODO: add support for column_id to column_name

            return state

    def _process_issues_event(self, webhookevent: GithubWebhookEvent):
        assert webhookevent.event_type == 'issues'
        githubissue = self._load_event_to_githubissue(webhookevent.event)

        # get related kippo project
        # -- NOTE: Currently a GithubIssue may only be assigned to 1 Project
        repository_api_url = githubissue.repository_url
        candidate_projects = list(KippoProject.objects.filter(kippotask_project__github_issue_api_url__startswith=repository_api_url))
        if len(candidate_projects) > 1:
            raise ValueError(f'More than 1 KippoProject found for Issue.repository_url={repository_api_url}: {[p.name for p in candidate_projects]}')
        elif len(candidate_projects) <= 0:
            raise ProjectNotFoundError(f'KippoProject NOT found for Issue.repository_url={repository_api_url}: {[p.name for p in candidate_projects]}')

        project = candidate_projects[0]
        issue_processor = self.get_organization_issue_processor(project.organization)
        is_new_task, new_taskstatus_entries, updated_taskstatus_entries = issue_processor.process(project, githubissue)
        return 'processed'

    def _process_issuecomment_event(self, webhookevent: GithubWebhookEvent):
        assert webhookevent.event_type == 'issue_comment'
        githubissue = self._load_event_to_githubissue(webhookevent.event)

        # populate GithubIssue.latest_comment* fields before processing
        # -- This enables kippo to process the GithubIssue through the standard processor
        comment = webhookevent.event['comment']
        githubissue.latest_comment_body = comment['body']
        githubissue.latest_comment_created_by = comment['user']['login']
        githubissue.latest_comment_created_at = comment['created_at']

        # get related kippo project
        # -- NOTE: Currently a GithubIssue may only be assigned to 1 Project
        repository_api_url = githubissue.repository_url
        candidate_projects = list(KippoProject.objects.filter(kippotask_project__github_issue_api_url__startswith=repository_api_url))
        if len(candidate_projects) > 1:
            raise ValueError(f'More than 1 KippoProject found for Issue.repository_url={repository_api_url}: {[p.name for p in candidate_projects]}')
        elif len(candidate_projects) <= 0:
            raise ProjectNotFoundError(f'KippoProject NOT found for Issue.repository_url={repository_api_url}: {[p.name for p in candidate_projects]}')

        project = candidate_projects[0]
        issue_processor = self.get_organization_issue_processor(project.organization)
        is_new_task, new_taskstatus_entries, updated_taskstatus_entries = issue_processor.process(project, githubissue)
        return 'processed'

    def process_webhook_events(self) -> Counter:
        unprocessed_events_for_update = GithubWebhookEvent.objects.filter(state='unprocessed').order_by('created_datetime')
        unprocessed_events = copy.copy(unprocessed_events_for_update)
        unprocessed_events_for_update.update(state='processing')
        eventtype_method_mapping = {
            'project_card': self._process_projectcard_event,
            'issues': self._process_issues_event,
            'issue_comment': self._process_issuecomment_event,
        }

        processed_events = Counter()
        for webhookevent in unprocessed_events:
            eventtype_processing_method = eventtype_method_mapping[webhookevent.event_type]
            try:
                result_state = eventtype_processing_method(webhookevent)
            except ProjectNotFoundError as e:
                logger.exception(e)
                logger.error(f'No related KippoProject found for task: {e.args}')
                result_state = 'error'
                webhookevent.event['kippoerror'] = f'No related project found for task!'
            webhookevent.state = result_state
            webhookevent.save()
            processed_events[webhookevent.event_type] += 1
        return processed_events



