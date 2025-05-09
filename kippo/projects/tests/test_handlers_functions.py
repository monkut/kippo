import json
from io import BytesIO

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from commons.tests.utils import reset_buckets
from django.test import TestCase
from django.utils import timezone

from kippo.awsclients import S3_CLIENT, parse_s3_uri, s3_key_exists
from projects.handlers.functions import _get_projectid_mapping_ignore_date, _prepare_mapping, write_projectid_json
from projects.models import ActiveKippoProject, KippoProject


class ProjectsHandlersFunctionsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        # create test bucket for mapping output
        self.test_bucket = "projects-handlers-functions-bucket"
        # S3_RESOURCE.create_bucket(Bucket=self.test_bucket, CreateBucketConfiguration={"LocationConstraint": settings.TARGET_REGION})
        # S3_RESOURCE.Bucket(self.test_bucket).objects.all().delete()
        reset_buckets([self.test_bucket])
        created_objects = setup_basic_project()

        # get active column state names
        self.user = created_objects["KippoUser"]
        self.project = created_objects["KippoProject"]
        self.project2 = created_objects["KippoProject2"]
        self.organization = created_objects["KippoOrganization"]

        self.test_s3uri = f"s3://{self.test_bucket}/test/mapping.json"

    def test__prepare_mapping(self):
        expected_initial_project_count = 2
        assert ActiveKippoProject.objects.count() == expected_initial_project_count, ActiveKippoProject.objects.count()

        # change active project to closed project
        closed_but_not_old_pk = self.project2.pk
        self.project2.is_closed = True

        self.project2.save()

        # add closed project
        old_kippo_project_api_id = "1234568"
        old_closed_project_name = "old-closed-project"
        lte_ignore_datetime = _get_projectid_mapping_ignore_date()
        old_actual_closed_date = lte_ignore_datetime - timezone.timedelta(days=1)
        old_kippo_project = KippoProject(
            organization=self.organization,
            name=old_closed_project_name,
            github_project_html_url=f"https://github.com/orgs/{self.organization.github_organization_name}/projects/3",
            github_project_api_url=f"https://api.github.com/projects/{old_kippo_project_api_id}",
            columnset=self.project.columnset,
            created_by=self.user,
            updated_by=self.user,
            is_closed=True,
            closed_datetime=old_actual_closed_date,
        )
        old_kippo_project.save()
        expected_closed_project_count = 2
        expected_open_project_count = 1
        assert KippoProject.objects.filter(is_closed=True).count() == expected_closed_project_count
        assert KippoProject.objects.filter(is_closed=False).count() == expected_open_project_count

        expected = {str(p.pk): p.name for p in KippoProject.objects.exclude(closed_datetime__lte=lte_ignore_datetime)}
        expected_project_count = 2
        assert len(expected) == expected_project_count, len(expected)
        mapping = _prepare_mapping()
        self.assertIn("last_updated", mapping)
        # remove "last_updated"
        del mapping["last_updated"]
        self.assertDictEqual(mapping, expected)

        expected = {str(closed_but_not_old_pk), str(self.project.pk)}
        self.assertEqual(set(mapping.keys()), expected)

    def test_write_projectid_json(self):
        expected_project_count = 2
        assert ActiveKippoProject.objects.count() == expected_project_count, ActiveKippoProject.objects.count()
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
