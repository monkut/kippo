import json
import logging
from io import BytesIO

from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone

from kippo.awsclients import S3_CLIENT, parse_s3_uri

from ..models import KippoProject

logger = logging.getLogger(__name__)


def _get_projectid_mapping_ignore_date() -> timezone.datetime:
    lte_ignore_datetime = timezone.now() - timezone.timedelta(days=settings.PROJECTID_MAPPING_CLOSED_IGNORED_DAYS)
    return lte_ignore_datetime


def _prepare_mapping() -> dict:
    now = timezone.now().replace(microsecond=0)
    mapping = {"last_updated": now.isoformat()}
    lte_ignore_datetime = _get_projectid_mapping_ignore_date()
    for project in KippoProject.objects.exclude(closed_datetime__lte=lte_ignore_datetime):
        mapping[str(project.pk)] = project.name
    return mapping


def write_projectid_json(projectid_mapping_json_s3uri: str) -> bool:
    logger.info("_prepare_mapping() ... ")
    mapping = _prepare_mapping()
    logger.debug(f"mapping={mapping}")
    logger.info("_prepare_mapping() ... DONE!")
    encoded_json_mapping_bytesio = BytesIO(json.dumps(mapping, indent=4, ensure_ascii=False).encode("utf8"))
    bucket, key = parse_s3_uri(projectid_mapping_json_s3uri)
    logger.info(f"uploading mapping file ({projectid_mapping_json_s3uri}) ... ")
    updated = False
    try:
        S3_CLIENT.upload_fileobj(encoded_json_mapping_bytesio, bucket, key)
        logger.info(f"uploading mapping file ({projectid_mapping_json_s3uri}) ... DONE!")
        updated = True
    except ClientError:
        logger.exception(f"uploading mapping file ({projectid_mapping_json_s3uri}) ... ERROR!")
    return updated


def handle_projectid_mapping(event: dict | None = None, context: dict | None = None):  # noqa: ARG001
    if settings.PROJECTID_MAPPING_JSON_S3URI:
        logger.info(f"PROJECTID_MAPPING_JSON_S3URI={settings.PROJECTID_MAPPING_JSON_S3URI}")
        write_projectid_json(projectid_mapping_json_s3uri=settings.PROJECTID_MAPPING_JSON_S3URI)
    else:
        logger.warning("PROJECTID_MAPPING_JSON_S3URI envar not defined, projectid_mapping json file will not be written!")


def run_weeklyprojectstatus(event: dict | None, context: dict | None) -> tuple[list, list]:  # noqa: ARG001
    """Run weekly project status."""
    from accounts.models import KippoOrganization

    from projects.managers import ProjectSlackManager

    organizations_with_reporting_enabled = KippoOrganization.objects.filter(enable_slack_channel_reporting=True)

    logger.info(f"len(organizations_with_reporting_enabled)={len(organizations_with_reporting_enabled)}")
    responses = []
    all_status_groups = []
    for organization in organizations_with_reporting_enabled:
        logger.info(f"Calling ProjectSlackManager.post_weekly_project_status() for ({organization.name}) ...")
        mgr = ProjectSlackManager(organization=organization)
        block_groups, web_client_response = mgr.post_weekly_project_status()
        all_status_groups.extend(block_groups)
        responses.append(web_client_response)
        logger.info(f"Calling ProjectSlackManager.post_weekly_project_status() for ({organization.name}) ... DONE")
    return all_status_groups, responses
