import importlib

from django.test import TestCase

migration_module = importlib.import_module("projects.migrations.0031_kippoproject_category_choices_close_fields")
map_invalid_categories_to_other = migration_module.map_invalid_categories_to_other


class FakeQuerySet:
    def __init__(self, projects: list[dict]) -> None:
        self._projects = projects

    def exclude(self, **kwargs):
        valid_values = kwargs["category__in"]
        return FakeQuerySet([p for p in self._projects if p["category"] not in valid_values])

    def update(self, **kwargs):
        for project in self._projects:
            for key, value in kwargs.items():
                project[key] = value
        return len(self._projects)


class FakeManager:
    def __init__(self, projects: list[dict]) -> None:
        self._projects = projects

    def exclude(self, **kwargs):
        return FakeQuerySet(self._projects).exclude(**kwargs)


class FakeKippoProject:
    def __init__(self, projects: list[dict]) -> None:
        self.objects = FakeManager(projects)


class FakeApps:
    def __init__(self, projects: list[dict]) -> None:
        self._model = FakeKippoProject(projects)

    def get_model(self, app_label: str, model_name: str):
        assert app_label == "projects"
        assert model_name == "KippoProject"
        return self._model


class CategoryDataMigrationTestCase(TestCase):
    def test_invalid_category_values_mapped_to_other(self):
        projects = [
            {"name": "p1", "category": "testing"},
            {"name": "p2", "category": "foo"},
            {"name": "p3", "category": "poc"},
            {"name": "p4", "category": "new-proposal"},
            {"name": "p5", "category": "upsell-improvement"},
        ]
        apps = FakeApps(projects)
        map_invalid_categories_to_other(apps, schema_editor=None)
        self.assertEqual(projects[0]["category"], "other")
        self.assertEqual(projects[1]["category"], "other")
        self.assertEqual(projects[2]["category"], "poc")
        self.assertEqual(projects[3]["category"], "new-proposal")
        self.assertEqual(projects[4]["category"], "upsell-improvement")
