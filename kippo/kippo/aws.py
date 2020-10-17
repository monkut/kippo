from typing import Tuple
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings

BOTO3_CONFIG = Config(connect_timeout=settings.BOTO3_CONNECT_TIMEOUT, retries={"max_attempts": 3})
SQS_CLIENT = boto3.client("sqs", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["sqs"])
SQS_RESOURCE = boto3.resource("sqs", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["sqs"])

S3_CLIENT = boto3.client("s3", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["s3"])
S3_RESOURCE = boto3.resource("s3", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["s3"])


def parse_s3_uri(uri: str) -> Tuple[str, str]:
    """
    Parse s3 uri (s3://bucket/key) to (bucket, key)
    """
    result = urlparse(uri)
    bucket = result.netloc
    key = result.path[1:]  # removes leading slash
    return bucket, key


def s3_key_exists(bucket: str, key: str) -> bool:
    """Check if given bucket, key exists"""
    exists = None
    try:
        S3_CLIENT.head_object(Bucket=bucket, Key=key)
        exists = True
    except ClientError as e:
        if e.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
            exists = False
        else:
            raise
    return exists
