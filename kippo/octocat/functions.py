import os
import logging
from distutils.util import strtobool
from collections import defaultdict

from zappa.asynchronous import task

from projects.models import KippoProject
from tasks.models import KippoTask
from tasks.periodic.tasks import collect_github_project_issues
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
                *_, unhandeled_issues = collect_github_project_issues(str(organization.id), github_project_urls=list(urls))
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
    if 'content_url' in event['project_card']:
        content_url = event['project_card']['content_url']
        logger.debug(f'incoming event content_url: {content_url}')
        try:
            kippo_task = KippoTask.objects.get(
                project__organization=organization,
                github_issue_api_url=content_url
            )
            project = kippo_task.project
        except KippoTask.DoesNotExist:
            logger.warning(f'No related KippoTask not found for content_url: {content_url}')

        webhook_event = GithubWebhookEvent(
            organization=organization,
            event_type=event_type,
            event=event,
        )
        webhook_event.save()
        logger.debug(f' -- webhookevent created: {event_type}:{event["action"]}!')
    else:
        logger.warning(f'SKIPPING -- "content_url" not found in: {event["project_card"]}')
        raise KeyError(f'"content_url" key not found in event["project_card"]!')

    return webhook_event


def process_webhook_events():
    unprocessed_events = GithubWebhookEvent.objects.filter(state='unprocessed').order_by('created_datetime')
    unprocessed_events.update(state='processing')
    for webhookevent in unprocessed_events:
        if 'project_card' in webhookevent.event:  # using 'project_card' key to identify event type
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

        webhookevent.state = state
        webhookevent.save()

