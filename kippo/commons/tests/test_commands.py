import shutil
from pathlib import Path

from accounts.models import KippoUser
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from commons.tests.utils import reset_buckets
from kippo.awsclients import get_s3_client


class S3CommandsTestCase(TestCase):
    def setUp(self):
        reset_buckets()
        self.user = KippoUser.objects.create_user(username="testuser", password="testpassword", email="")  # noqa: S106

    def test_dump_and_load(self):
        expected_user_count = 1
        assert KippoUser.objects.count() == expected_user_count

        # Pre-Check: Confirm that there is not data in the bucket
        response = get_s3_client().list_objects_v2(
            Bucket=settings.DUMPDATA_S3_BUCKETNAME,
            Prefix=settings.DUMPDATA_S3_KEY_PREFIX,
        )
        bucket_items = response.get("Contents", [])
        assert len(bucket_items) == 0

        call_command("dumpdata_to_s3", bucket=settings.DUMPDATA_S3_BUCKETNAME)

        # Confirm that file is generated in the bucket
        response = get_s3_client().list_objects_v2(
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


class CreateApiClientCommandTestCase(TestCase):
    """Test case for create_api_client management command."""

    def setUp(self):
        """Set up test fixtures."""
        self.base_dir = Path(settings.BASE_DIR).parent
        self.client_dir = self.base_dir / "python-client"

    def tearDown(self):
        """Clean up generated client directory after tests."""
        if self.client_dir.exists():
            shutil.rmtree(self.client_dir)

    def test_create_api_client_generates_client_directory(self):
        """Test that create_api_client command generates the client directory."""
        # Ensure clean state
        if self.client_dir.exists():
            shutil.rmtree(self.client_dir)

        # Run the command
        call_command("create_api_client", "--cleanup")

        # Verify client directory was created
        self.assertTrue(self.client_dir.exists())
        self.assertTrue(self.client_dir.is_dir())

    def test_create_api_client_generates_required_files(self):
        """Test that create_api_client command generates required client files."""
        # Ensure clean state
        if self.client_dir.exists():
            shutil.rmtree(self.client_dir)

        # Run the command
        call_command("create_api_client", "--cleanup")

        # Verify key files exist
        required_files = [
            self.client_dir / "__init__.py",
            self.client_dir / "client.py",
            self.client_dir / "errors.py",
            self.client_dir / "types.py",
        ]

        for required_file in required_files:
            self.assertTrue(
                required_file.exists(),
                f"Required file not found: {required_file}",
            )

    def test_create_api_client_generates_api_module(self):
        """Test that create_api_client command generates api module."""
        # Ensure clean state
        if self.client_dir.exists():
            shutil.rmtree(self.client_dir)

        # Run the command
        call_command("create_api_client", "--cleanup")

        # Verify api directory exists
        api_dir = self.client_dir / "api"
        self.assertTrue(api_dir.exists())
        self.assertTrue(api_dir.is_dir())

        # Verify api subdirectories exist
        projects_api_dir = api_dir / "projects"
        self.assertTrue(projects_api_dir.exists())

    def test_create_api_client_generates_models_module(self):
        """Test that create_api_client command generates models module."""
        # Ensure clean state
        if self.client_dir.exists():
            shutil.rmtree(self.client_dir)

        # Run the command
        call_command("create_api_client", "--cleanup")

        # Verify models directory exists
        models_dir = self.client_dir / "models"
        self.assertTrue(models_dir.exists())
        self.assertTrue(models_dir.is_dir())

    def test_create_api_client_cleanup_flag(self):
        """Test that --cleanup flag removes existing client directory."""
        # Create a dummy file in the client directory
        self.client_dir.mkdir(parents=True, exist_ok=True)
        dummy_file = self.client_dir / "dummy.txt"
        dummy_file.write_text("This should be removed")

        # Run command with cleanup
        call_command("create_api_client", "--cleanup")

        # Verify old file is gone and new client exists
        self.assertFalse(dummy_file.exists())
        self.assertTrue((self.client_dir / "__init__.py").exists())
