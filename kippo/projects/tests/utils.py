from typing import List, Optional
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
from django.conf import settings
from kippo.aws import S3_RESOURCE


class MockRequest:
    GET = {}
    POST = {}
    path = ""
    _messages = MagicMock()

    def __init__(self, *args, **kwargs):
        self.GET = {}
        self.POST = {}
        self._messages = MagicMock()

    def get_full_path(self):
        return self.path


def reset_buckets() -> List[Optional[str]]:
    """
    Ensure a empty bucket.

    Create a newly s3 bucket if it does not exists and remove all items.
    """
    buckets = []
    for bucket_name in (settings.DUMPDATA_S3_BUCKETNAME,):

        try:
            S3_RESOURCE.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": settings.AWS_DEFAULT_REGION})
        except ClientError:
            pass
        S3_RESOURCE.Bucket(bucket_name).objects.all().delete()
        buckets.append(bucket_name)
    return buckets
