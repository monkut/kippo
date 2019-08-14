import os
import copy
import json
import logging
from typing import Generator
from distutils.util import strtobool
from collections import Counter

from django.utils import timezone
from django.conf import settings

from ghorgs.wrappers import GithubIssue

from projects.models import KippoProject
from tasks.models import KippoTask, KippoTaskStatus
from tasks.exceptions import ProjectNotFoundError
from tasks.periodic.tasks import OrganizationIssueProcessor

from accounts.models import KippoOrganization, KippoUser
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
        self.github_manager_kippouser = KippoUser.objects.get(username=settings.GITHUB_MANAGER_USERNAME)

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
        Process the 'project_card' event and update the related KippoTaskStatus.state field
        > If KippoTaskStatus does not exist for the current date create one based on the 'latest'.
        :param webhookevent:
        """
        assert webhookevent.event_type == 'project_card'

        # identify project, retrieve related KippoProject
        github_project_api_url = webhookevent.event['project_card']['project_url']
        try:
            kippo_project = KippoProject.objects.get(github_project_api_url=github_project_api_url)
        except KippoProject.DoesNotExist as e:
            logger.exception(e)
            logger.error(f'GithubWebhookEvent({webhookevent}) related KippoProject not found: event.project_card.project_url={github_project_api_url}')
            state = 'error'
            return state

        if kippo_project:
            if 'content_url' not in webhookevent.event['project_card']:
                logger.warning(f'webhookevent({webhookevent.id}).event does not contain "content_url" key, IGNORE (notes not supported)!')
                state = 'error'
            else:
                task_api_url = webhookevent.event['project_card']['content_url']
                github_column_id = webhookevent.event['project_card']['column_id']
                columnid2name_mapping = kippo_project.get_columnset_id_to_name_mapping()
                column_name = columnid2name_mapping.get(github_column_id, None)
                if not column_name:
                    logger.error(f'column_name for column_id({github_column_id}) not found in KippProject.get_columnset_id_to_name_mapping() for column_id({github_column_id}: {columnid2name_mapping}')
                    state = 'error'
                else:
                    # 'column_name' is used to manage KippoTask state
                    # TODO: Add support for 'move' event
                    #  update to properly update state based on column position in order to ignore estimates for tasks in 'non-active' columns

                    # github_from_column_id = webhookevent.event['changes']['column_id']['from']  # ex: 4162976
                    state = 'processed'
                    current_action = webhookevent.event['action']
                    if current_action in ('created', 'converted', 'moved'):
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

                        try:
                            task = KippoTask.objects.get(github_issue_api_url=task_api_url)
                        except KippoTask.DoesNotExist as e:
                            logger.exception(e)
                            state = 'error'
                            msg = f'KippoTask.github_issue_api_url={task_api_url} DoesNotExist, not updated on webhookevent "{current_action}" action'
                            logger.error(msg)
                            webhookevent.event['kippoerror'] = msg
                            return state

                        # update task.project_card_id
                        card_id = webhookevent.event['project_card']['id']
                        if task.project_card_id != card_id:
                            # Don't expect this to happen, as a KippoTask *should* only belong to 1 project
                            logger.warning(f'Current KippoTask.project_card_id({task.project_card_id}) != card_id({card_id}): {task}')
                        task.project_card_id = card_id
                        task.save()

                        # create/update KippoTaskStatus
                        # KippoTask created with 'issues' event
                        # -- update 'state' info if KippoTaskStatus exists
                        try:
                            status = KippoTaskStatus.objects.filter(task=task).latest('created_datetime')
                        except KippoTaskStatus.DoesNotExist as e:
                            logger.exception(e)
                            state = 'error'
                            msg = f'KippoTaskStatus.DoesNotExist, "state" not updated!'
                            logger.error(msg)
                            webhookevent.event['kippoerror'] = msg
                            return state

                        effort_date = timezone.now().date()
                        if status.effort_date != effort_date:
                            # create a new KippoTaskStatus Entry
                            status = KippoTaskStatus(
                                task=task,
                                effort_date=effort_date,
                                state_priority=status.state_priority,
                                minimum_estimate_days=status.minimum_estimate_days,
                                estimate_days=status.estimate_days,
                                maximum_estimate_days=status.maximum_estimate_days,
                                hours_spent=status.hours_spent,
                                tags=status.tags,
                                comment=status.comment,
                                created_by=self.github_manager_kippouser,
                                updated_by=self.github_manager_kippouser,
                            )
                        # update column state!
                        status.state = column_name
                        status.save()
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

    def _get_events(self) -> Generator:
        # process event_types in the following order
        #  - To assure that task/taskstatus exist before processing 'issue_comment' & 'project_card' events
        event_types_to_process = (
            'issues',
            'issue_comment',
            'project_card',
        )

        for event_type in event_types_to_process:
            unprocessed_events_for_update = GithubWebhookEvent.objects.filter(
                state='unprocessed',
                event_type=event_type,
            ).order_by(
                'created_datetime'
            )
            unprocessed_events = copy.copy(unprocessed_events_for_update)
            unprocessed_events_for_update.update(state='processing')
            yield from unprocessed_events

    def process_webhook_events(self) -> Counter:
        processed_events = Counter()
        unprocessed_events = self._get_events()
        eventtype_method_mapping = {
            'project_card': self._process_projectcard_event,
            'issues': self._process_issues_event,
            'issue_comment': self._process_issuecomment_event,
        }

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



