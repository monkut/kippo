import csv
from collections import OrderedDict
from io import BytesIO, StringIO
from typing import Dict, Generator, List, Tuple
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


def upload_s3_csv(bucket: str, key: str, headers: Dict[str, str], row_generator: Generator) -> Tuple[str, str]:
    fieldnames = headers.values()
    with StringIO() as csvout:
        writer = csv.DictWriter(csvout, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(row_generator)
        csvout.seek(0)
        # encode to utf8 fileobj
        bytesout = BytesIO(csvout.read().encode("utf8"))
        bytesout.seek(0)
        S3_CLIENT.upload_fileobj(bytesout, bucket, key)
    return bucket, key


def download_s3_csv(bucket: str, key: str) -> List[OrderedDict]:
    with BytesIO() as bytesin:
        S3_CLIENT.download_fileobj(bucket, key, bytesin)
        bytesin.seek(0)
        stringin = StringIO(bytesin.read().decode("utf8"))
        reader = csv.DictReader(stringin)
        rows = [row for row in reader]
    return rows
