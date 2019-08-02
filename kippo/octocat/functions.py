import os
import logging
from distutils.util import strtobool
from collections import defaultdict
from typing import Tuple

from zappa.asynchronous import task
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

            organization_specific_github_projects[event.related_project.organization].add(event.related_project.github_project_url)

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


def queue_incoming_project_card_event(organization: KippoOrganization, event: dict) -> GithubWebhookEvent:
    # card should contain a 'content_url' representing the issue attached (if an issue card)
    # - Use the 'content_url' to retrieve the internally managed issue,
    # - find the related project and issue an update for that project
    #   (Overkill, but for now this is the cleanest way without a ghorgs re-write)

    # Accept any event (ignoring action)
    if 'content_url' in event['project_card']:
        content_url = event['project_card']['content_url']
        logger.debug(f'incoming event content_url: {content_url}')
        project = None
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
            event=event,
            related_project=project
        )
        webhook_event.save()
    else:
        logger.warning(f'SKIPPING -- "content_url" not found in: {event["project_card"]}')
        raise ValueError(f'"content_url" not found in event["project_card"]: {event}')

    return webhook_event
