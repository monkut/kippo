import json
from io import BytesIO

from common.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from kippo.aws import S3_CLIENT, S3_RESOURCE, parse_s3_uri, s3_key_exists
from projects.handlers.functions import _prepare_mapping, write_projectid_json
from projects.models import ActiveKippoProject


class ProjectsHandlersFunctionsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        # create test bucket for mapping output
        self.test_bucket = "projects-handlers-functions-bucket"
        S3_RESOURCE.create_bucket(Bucket=self.test_bucket)
        S3_RESOURCE.Bucket(self.test_bucket).objects.all().delete()
        created_objects = setup_basic_project()

        # get active column state names
        self.project = created_objects["KippoProject"]

        self.test_s3uri = f"s3://{self.test_bucket}/test/mapping.json"

    def test__prepare_mapping(self):
        assert ActiveKippoProject.objects.count() == 2, ActiveKippoProject.objects.count()
        expected = {str(p.pk): p.name for p in ActiveKippoProject.objects.all()}
        mapping = _prepare_mapping()
        self.assertIn("last_updated", mapping)
        # remove "last_updated"
        del mapping["last_updated"]
        self.assertDictEqual(mapping, expected)

    def test_write_projectid_json(self):
        assert ActiveKippoProject.objects.count() == 2, ActiveKippoProject.objects.count()
        expected = {str(p.pk): p.name for p in ActiveKippoProject.objects.all()}

        result = write_projectid_json(projectid_mapping_json_s3uri=self.test_s3uri)
        self.assertTrue(result)

        bucket, key = parse_s3_uri(self.test_s3uri)
        self.assertTrue(s3_key_exists(bucket, key))

        filebytes = BytesIO()
        S3_CLIENT.download_fileobj(bucket, key, filebytes)
        filebytes.seek(0)
        mapping = json.loads(filebytes.read())

        self.assertIn("last_updated", mapping)
        # remove "last_updated"
        del mapping["last_updated"]
        self.assertDictEqual(mapping, expected)
