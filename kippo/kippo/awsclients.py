import csv
from collections.abc import Generator
from functools import lru_cache
from http import HTTPStatus
from io import BytesIO, StringIO
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings

BOTO3_CONFIG = Config(connect_timeout=settings.BOTO3_CONNECT_TIMEOUT, retries={"max_attempts": 3})


# boto3 clients/resources are lazily built and cached per warm process. Building them
# at import time added ~50-150ms each to Lambda cold-start (awsclients is imported during
# URLconf load via projects/views.py). A boto3 client is stateless and thread-safe, so
# caching one per container is standard practice and does not depend on cross-request
# persistence for correctness.
@lru_cache(maxsize=1)
def get_sqs_client():  # noqa: ANN201
    return boto3.client("sqs", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["sqs"])


@lru_cache(maxsize=1)
def get_sqs_resource():  # noqa: ANN201
    return boto3.resource("sqs", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["sqs"])


@lru_cache(maxsize=1)
def get_s3_client():  # noqa: ANN201
    return boto3.client("s3", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["s3"])


@lru_cache(maxsize=1)
def get_s3_resource():  # noqa: ANN201
    return boto3.resource("s3", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["s3"])


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse s3 uri (s3://bucket/key) to (bucket, key)"""
    result = urlparse(uri)
    bucket = result.netloc
    key = result.path[1:]  # removes leading slash
    return bucket, key


def s3_key_exists(bucket: str, key: str) -> bool:
    """Check if given bucket, key exists"""
    exists = None
    try:
        get_s3_client().head_object(Bucket=bucket, Key=key)
        exists = True
    except ClientError as e:
        if e.response["ResponseMetadata"]["HTTPStatusCode"] == HTTPStatus.NOT_FOUND:
            exists = False
        else:
            raise
    return exists


def upload_s3_csv(bucket: str, key: str, headers: dict[str, str], row_generator: Generator) -> tuple[str, str]:
    fieldnames = headers.values()
    with StringIO() as csvout:
        writer = csv.DictWriter(csvout, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(row_generator)
        csvout.seek(0)
        # encode to utf8 fileobj
        bytesout = BytesIO(csvout.read().encode("utf8"))
        bytesout.seek(0)
        get_s3_client().upload_fileobj(bytesout, bucket, key)
    return bucket, key


def download_s3_csv(bucket: str, key: str) -> list[dict]:
    with BytesIO() as bytesin:
        get_s3_client().download_fileobj(bucket, key, bytesin)
        bytesin.seek(0)
        stringin = StringIO(bytesin.read().decode("utf8"))
        reader = csv.DictReader(stringin)
        rows = list(reader)
    return rows
