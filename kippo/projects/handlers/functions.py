import json
import logging
from io import BytesIO

from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone
from kippo.aws import S3_CLIENT, parse_s3_uri

from ..models import KippoProject

logger = logging.getLogger(__name__)


def _prepare_mapping() -> dict:
    now = timezone.now().replace(microsecond=0)
    mapping = {"last_updated": now.isoformat()}
    for project in KippoProject.objects.all():
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
    except ClientError as e:
        logger.exception(e)
        logger.error(f"uploading mapping file ({projectid_mapping_json_s3uri}) ... ERROR!")
    return updated


def handle_projectid_mapping(event=None, context=None):
    if settings.PROJECTID_MAPPING_JSON_S3URI:
        logger.info(f"PROJECTID_MAPPING_JSON_S3URI={settings.PROJECTID_MAPPING_JSON_S3URI}")
        write_projectid_json(projectid_mapping_json_s3uri=settings.PROJECTID_MAPPING_JSON_S3URI)
    else:
        logger.warning("PROJECTID_MAPPING_JSON_S3URI envar not defined, projectid_mapping json file will not be written!")
