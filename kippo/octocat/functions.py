import logging
from time import sleep
from collections import defaultdict

from zappa import task
from tasks.models import KippoTask
from tasks.periodic.tasks import collect_github_project_issues
from .models import GithubWebhookEvent


logger = logging.getLogger(__name__)

THREE_MINUTES = 3 * 60


@task
def process_unprocessed_events():
    events = GithubWebhookEvent.objects.filter(state='unprocessed')
    if events:
        events.update(state='processing')

        # get unique projects from events
        organization_specific_github_projects = defaultdict(set)
        for event in events:
            organization_specific_github_projects[event.related_project.organization.id].add(event.related_project.github_project_url)

        for organization, urls in organization_specific_github_projects:
            logger.info(f'Webhook Triggered collect_github_project_issues() ({organization.id}) {organization.name}: {urls}')
            collect_github_project_issues(organization, github_project_urls=list(urls))
        events.update(state='processed')


@task
def wait():
    """
    Buffer to wait while user makes multiple changes to reduce updates per project
    :return:
    """
    sleep(THREE_MINUTES)
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
            wait()
        except KippoTask.DoesNotExist:
            logger.warning(f'Related KippoTask not found for content_url: {content_url}')
    else:
        logger.warning(f'SKIPPING -- "content_url" not found in: {event["project_card"]}')