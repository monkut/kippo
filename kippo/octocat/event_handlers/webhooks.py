import datetime
import logging
from collections import Counter

from django.conf import settings
from django.utils import timezone

from ..functions import GithubWebhookProcessor
from ..models import GithubWebhookEvent

logger = logging.getLogger(__name__)


def process_webhooks(event: dict, context: dict) -> tuple[Counter, int]:  # noqa: ARG001
    processor = GithubWebhookProcessor()

    # process existing hooks
    processed_events = processor.process_webhook_events()

    # delete old processed hooks
    now = timezone.now()
    delete_datetime = now - datetime.timedelta(days=settings.WEBHOOK_DELETE_DAYS)
    logger.info(f"delete_datetime={delete_datetime}")
    kwargs = {"created_datetime__lte": delete_datetime, "state": "processed"}
    old_githubwebhookevent_count = GithubWebhookEvent.objects.filter(**kwargs).count()
    if old_githubwebhookevent_count:
        logger.info(f'deleting ({old_githubwebhookevent_count}) old state="processed" GithubWebhookEvent(s)...')
        GithubWebhookEvent.objects.filter(**kwargs).delete()
        logger.info(f"({old_githubwebhookevent_count}) deleted!")
    return processed_events, old_githubwebhookevent_count
