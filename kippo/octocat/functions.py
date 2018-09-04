import os
import logging
from time import sleep
from distutils.util import strtobool
from collections import defaultdict

from zappa.async import task
from tasks.models import KippoTask
from tasks.periodic.tasks import collect_github_project_issues
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
            organization_specific_github_projects[event.related_project.organization].add(event.related_project.github_project_url)

        if not KIPPO_TESTING:
            logger.info('Starting Organization Github Processing....')
            for organization, urls in organization_specific_github_projects.items():
                logger.info(f'Webhook Triggered collect_github_project_issues() ({organization.id}) {organization.name}: {urls}')
                collect_github_project_issues(organization, github_project_urls=list(urls))

        # Update after processing
        GithubWebhookEvent.objects.filter(id__in=event_ids).update(state='processed')
    return event_ids


@task
def wait_and_process():
    """
    Buffer to wait while user makes multiple changes to reduce updates per project
    :return:
    """
    seconds = int(os.getenv('KIPPO_WEBHOOK_WAIT_SECONDS', THREE_MINUTES))
    sleep(seconds)
    process_unprocessed_events()


def process_incoming_project_card_event(event):
    # card should contain a 'content_url' representing the issue attached (if an issue card)
    # - Use the 'content_url' to retrieve the internally managed issue,
    # - find the related project and issue an update for that project
    #   (Overkill, but for now this is the cleanest way without a ghorgs re-write)

    # Accept any event (ignoring action)
    if 'content_url' in event['project_card']:
        content_url = event['project_card']['content_url']
        try:
            kippo_task = KippoTask.objects.get(github_issue_api_url=content_url)
            e = GithubWebhookEvent(event=event,
                                   related_project=kippo_task.project)
            e.save()
            wait_and_process()
        except KippoTask.DoesNotExist:
            logger.warning(f'Related KippoTask not found for content_url: {content_url}')
    else:
        logger.warning(f'SKIPPING -- "content_url" not found in: {event["project_card"]}')
