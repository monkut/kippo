from contextlib import suppress
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
from django.conf import settings

from kippo.awsclients import S3_RESOURCE


class MockRequest:
    GET = {}
    POST = {}
    path = ""
    _messages = MagicMock()

    def __init__(self, *args, **kwargs) -> None:
        self.GET = {}
        self.POST = {}
        self._messages = MagicMock()

    def get_full_path(self):
        return self.path


def reset_buckets(bucket_names: list | tuple | None = None) -> list[str]:
    """
    Ensure a empty bucket.

    Create a newly s3 bucket if it does not exists and remove all items.
    """
    if bucket_names is None:
        bucket_names = (settings.DUMPDATA_S3_BUCKETNAME,)

    created_buckets = []
    for bucket_name in bucket_names:
        with suppress(ClientError):
            S3_RESOURCE.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": settings.TARGET_REGION})
            created_buckets.append(bucket_name)
        S3_RESOURCE.Bucket(bucket_name).objects.all().delete()
    return created_buckets
