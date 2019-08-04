import os
import copy
import json
import logging
from distutils.util import strtobool
from collections import defaultdict

from zappa.asynchronous import task
from django.utils import timezone

from ghorgs.wrappers import GithubIssue

from projects.models import KippoProject
from tasks.models import KippoTask
from tasks.exceptions import ProjectNotFoundError
from tasks.periodic.tasks import collect_github_project_issues, OrganizationIssueProcessor
from accounts.models import KippoOrganization
from .models import GithubWebhookEvent


logger = logging.getLogger(__name__)

KIPPO_TESTING = strtobool(os.getenv('KIPPO_TESTING', 'False'))
THREE_MINUTES = 3 * 60


@task
def process_unprocessed_events():
    events = GithubWebhookEvent.objects.filter(state='unprocessed')
    event_ids = []
    if events:
        event_ids = [e.id for e in events]
        GithubWebhookEvent.objects.filter(id__in=event_ids).update(state='processing')

        # get unique projects from events
        organization_specific_github_projects = defaultdict(set)
        for event in events:
            # created, edited, moved, converted, or deleted
            # https://developer.github.com/v3/activity/events/types/#projectcardevent
            if event.action == 'created':
                raise NotADirectoryError
            elif event.action == 'edited':
                # update title/assignees/comment, etc
                # --> check 'changes'
                raise NotADirectoryError
            elif event.action == 'moved':
                # update state to column-state
                raise NotADirectoryError
            elif event.action == 'converted':
                # --> check 'changes'
                raise NotADirectoryError
            elif event.action == 'deleted':
                # update status
                raise NotADirectoryError

            organization_specific_github_projects[event.related_project.organization].add(event.related_project.github_project_html_url)

        if not KIPPO_TESTING:
            logger.info('Starting Organization Github Processing....')
            for organization, urls in organization_specific_github_projects.items():
                logger.info(f'Webhook Triggered collect_github_project_issues() ({organization.id}) {organization.name}: {urls}')
                *_, unhandeled_issues = collect_github_project_issues(str(organization.id), github_project_html_urls=list(urls))
                for issue, error_args in unhandeled_issues:
                    logger.error(f'Error handling issue({issue.html_url}): {error_args}')
        # Update after processing
        GithubWebhookEvent.objects.filter(id__in=event_ids).update(state='processed')
    return event_ids


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
                except KippoTask.DoesNotExist:
                    state = 'error'
                    msg = f'KippoTask.github_issue_api_url={task_api_url} DoesNotExist, not updated on webhookevent "move" action'
                    logger.error(msg)
                    webhookevent.event['kippoerror'] = msg
                # TODO: add support for column_id to column_name
                # create KippoTaskStatus
                raise NotImplementedError
            elif webhookevent.event['action'] == 'converted':
                # (NEW) Task create new kippo task in related project
                try:
                    project = KippoProject.objects.get(github_project_api_url=github_project_api_url)
                except KippoProject.DoesNotExist:
                    state = 'error'
                    msg = f'KippoProject.github_project_api_url={github_project_api_url} DoesNotExist, KippoTask *NOT CREATED*!'
                    logger.error(msg)
                    webhookevent.event['kippoerror'] = msg
                # TODO: get task details via github manager (need to add 'get_task_details()' method to ghorgs?)
                # create KippoTask

                # create KippoTaskStatus
                raise NotImplementedError

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
        raise NotImplementedError

    def process_webhook_events(self) -> int:
        unprocessed_events_for_update = GithubWebhookEvent.objects.filter(state='unprocessed').order_by('created_datetime')
        unprocessed_events = copy.copy(unprocessed_events_for_update)
        unprocessed_events_for_update.update(state='processing')
        eventtype_method_mapping = {
            'project_card': self._process_projectcard_event,
            'issues': self._process_issues_event,
            'issue_comment': self._process_issuecomment_event,
        }

        processed_events = 0
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
            processed_events += 1
        return processed_events



