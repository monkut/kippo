import logging

from django.conf import settings
from django.utils import timezone

from ..models import KippoTaskStatus

logger = logging.getLogger(__name__)


def delete(event, context):
    now = timezone.now()
    older_than_date = now - timezone.timedelta(days=settings.DELETE_DAYS)
    logger.debug(f"DELETE_DAYS={settings.DELETE_DAYS}")
    logger.info(f"deleting KippoTaskStatus older than {older_than_date} with closed project...")
    deleted_count, deleted_entries = KippoTaskStatus.objects.filter(created_datetime__lte=older_than_date, task__project__is_closed=True).delete()
    logger.info(f"-- {deleted_count} KippoTaskStatus deleted")
    logger.info(f"deleting KippoTaskStatus older than {older_than_date}...DONE")
