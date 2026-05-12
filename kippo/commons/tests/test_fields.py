"""Tests for `commons.fields.CommaSeparatedCharField` and `CommaSeparatedField`."""

from django.core import exceptions
from django.db import connection
from django.test import SimpleTestCase, TestCase
from projects.models import KippoProject

from commons.fields import CommaSeparatedCharField, CommaSeparatedField
from commons.tests import DEFAULT_FIXTURES, setup_basic_project


class CommaSeparatedCharFieldParsingTestCase(SimpleTestCase):
    """Unit tests for the model field's parse/serialize helpers (no DB)."""

    def setUp(self) -> None:
        self.field = CommaSeparatedCharField(max_length=255)

    def test_to_python_csv_string_returns_list(self):
        self.assertEqual(self.field.to_python("foo,bar,baz"), ["foo", "bar", "baz"])

    def test_to_python_list_passes_through(self):
        self.assertEqual(self.field.to_python(["foo", "bar"]), ["foo", "bar"])

    def test_to_python_empty_string_returns_empty_list(self):
        self.assertEqual(self.field.to_python(""), [])

    def test_to_python_none_returns_empty_list(self):
        self.assertEqual(self.field.to_python(None), [])

    def test_to_python_strips_whitespace(self):
        self.assertEqual(self.field.to_python(" foo , bar , baz "), ["foo", "bar", "baz"])

    def test_to_python_drops_empty_segments(self):
        self.assertEqual(self.field.to_python("foo,,bar, ,baz"), ["foo", "bar", "baz"])

    def test_to_python_invalid_type_raises_validation_error(self):
        with self.assertRaises(exceptions.ValidationError):
            self.field.to_python(42)

    def test_get_prep_value_list_joins_with_comma(self):
        self.assertEqual(self.field.get_prep_value(["foo", "bar"]), "foo,bar")

    def test_get_prep_value_csv_string_is_idempotent(self):
        self.assertEqual(self.field.get_prep_value("foo,bar"), "foo,bar")

    def test_get_prep_value_empty_list_returns_empty_string(self):
        self.assertEqual(self.field.get_prep_value([]), "")

    def test_get_prep_value_normalizes_whitespace_and_empties(self):
        # Storing the canonical form lets the DB hold predictable values.
        self.assertEqual(self.field.get_prep_value([" foo ", "", "bar"]), "foo,bar")

    def test_from_db_value_returns_list(self):
        self.assertEqual(self.field.from_db_value("foo,bar", None, None), ["foo", "bar"])

    def test_from_db_value_empty_returns_empty_list(self):
        self.assertEqual(self.field.from_db_value("", None, None), [])

    def test_formfield_renders_list_as_csv_in_widget(self):
        form_field = self.field.formfield()
        # The form field's prepare_value joins list values for widget display.
        self.assertEqual(form_field.prepare_value(["foo", "bar"]), "foo,bar")
        # String inputs (e.g. resubmitted form data) pass through unchanged.
        self.assertEqual(form_field.prepare_value("foo,bar"), "foo,bar")


class CommaSeparatedSerializerFieldTestCase(SimpleTestCase):
    """Unit tests for the DRF serializer field."""

    def setUp(self) -> None:
        self.field = CommaSeparatedField(max_length=255, allow_blank=True, required=False)

    def test_to_representation_joins_list(self):
        self.assertEqual(self.field.to_representation(["foo", "bar"]), "foo,bar")

    def test_to_representation_empty_list_returns_empty_string(self):
        self.assertEqual(self.field.to_representation([]), "")

    def test_to_representation_string_value_passes_through(self):
        # Defensive: if attribute hasn't been reloaded from DB yet, value may still be a string.
        self.assertEqual(self.field.to_representation("foo,bar"), "foo,bar")

    def test_to_internal_value_passes_csv_string_through(self):
        # The model field's get_prep_value normalizes on save; serializer just hands off the string.
        self.assertEqual(self.field.run_validation("foo,bar"), "foo,bar")


class KippoProjectDocbaseTagRoundTripTestCase(TestCase):
    """End-to-end round-trip through the KippoProject.docbase_tag column."""

    fixtures = DEFAULT_FIXTURES

    @classmethod
    def setUpTestData(cls) -> None:
        created = setup_basic_project()
        cls.project = created["KippoProject"]

    def test_assign_list_save_reload_returns_list(self):
        self.project.docbase_tag = ["foo", "bar", "baz"]
        self.project.save()
        reloaded = KippoProject.objects.get(pk=self.project.pk)
        self.assertEqual(reloaded.docbase_tag, ["foo", "bar", "baz"])

    def test_assign_csv_string_save_reload_returns_list(self):
        self.project.docbase_tag = "foo,bar,baz"
        self.project.save()
        reloaded = KippoProject.objects.get(pk=self.project.pk)
        self.assertEqual(reloaded.docbase_tag, ["foo", "bar", "baz"])

    def test_db_storage_is_canonical_csv_string(self):
        self.project.docbase_tag = [" foo ", "", "bar"]
        self.project.save()
        # Use raw SQL to bypass from_db_value and inspect the literal column contents.
        with connection.cursor() as cursor:
            cursor.execute("SELECT docbase_tag FROM projects_kippoproject WHERE id = %s", [str(self.project.pk)])
            raw = cursor.fetchone()[0]
        self.assertEqual(raw, "foo,bar")

    def test_empty_list_round_trips_as_empty(self):
        self.project.docbase_tag = []
        self.project.save()
        reloaded = KippoProject.objects.get(pk=self.project.pk)
        self.assertEqual(reloaded.docbase_tag, [])

    def test_default_value_is_empty_list_after_reload(self):
        # New project created without setting docbase_tag → DB default "" → reload returns [].
        new_project = KippoProject.objects.create(
            organization=self.project.organization,
            name="docbase-default-test",
            github_project_html_url="https://github.com/orgs/myorg/projects/99",
            github_project_api_nodeid="PVT_kwDO_DEFAULT",
            columnset=self.project.columnset,
            created_by=self.project.created_by,
            updated_by=self.project.updated_by,
        )
        reloaded = KippoProject.objects.get(pk=new_project.pk)
        self.assertEqual(reloaded.docbase_tag, [])
