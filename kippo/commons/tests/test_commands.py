from accounts.models import KippoUser
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from commons.tests.utils import reset_buckets
from kippo.awsclients import S3_CLIENT


class S3CommandsTestCase(TestCase):
    def setUp(self):
        reset_buckets()
        self.user = KippoUser.objects.create_user(username="testuser", password="testpassword", email="")  # noqa: S106

    def test_dump_and_load(self):
        expected_user_count = 1
        assert KippoUser.objects.count() == expected_user_count

        # Pre-Check: Confirm that there is not data in the bucket
        response = S3_CLIENT.list_objects_v2(
            Bucket=settings.DUMPDATA_S3_BUCKETNAME,
            Prefix=settings.DUMPDATA_S3_KEY_PREFIX,
        )
        bucket_items = response.get("Contents", [])
        assert len(bucket_items) == 0

        call_command("dumpdata_to_s3", bucket=settings.DUMPDATA_S3_BUCKETNAME)

        # Confirm that file is generated in the bucket
        response = S3_CLIENT.list_objects_v2(
            Bucket=settings.DUMPDATA_S3_BUCKETNAME,
            Prefix=settings.DUMPDATA_S3_KEY_PREFIX,
        )
        actual_bucket_items = response.get("Contents", [])
        expected_bucket_item_count = 1
        self.assertEqual(len(actual_bucket_items), expected_bucket_item_count)
        key = actual_bucket_items[0]["Key"]
        assert key

        # -- remove user and load
        KippoUser.objects.all().delete()

        expected_user_count = 0
        assert KippoUser.objects.count() == expected_user_count

        # call "loaddata_from_s3" command
        call_command("loaddata_from_s3", s3_key=key)

        # Confirm that the user is restored
        expected_user_count = 1
        acutal_user_count = KippoUser.objects.count()
        self.assertEqual(acutal_user_count, expected_user_count)
