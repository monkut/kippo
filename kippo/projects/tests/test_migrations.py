import importlib

from accounts.models import KippoOrganization, KippoUser
from django.apps import apps as django_apps
from django.test import TestCase

from projects.models import KippoProject, KippoProjectOrganizationCategory, ProjectColumnSet

migration_module = importlib.import_module("projects.migrations.0031_kippoproject_category_choices_close_fields")
map_invalid_categories_to_other = migration_module.map_invalid_categories_to_other

reconcile_module = importlib.import_module("projects.migrations.0052_reconcile_category_taxonomy")
reconcile = reconcile_module.reconcile
SYSTEM_DEV_KEY = reconcile_module.SYSTEM_DEV_KEY
UPSELL_KEYS = reconcile_module.UPSELL_KEYS
RETIRED_KEYS = reconcile_module.RETIRED_KEYS
CONTINUATION_LEAD_SOURCE = reconcile_module.CONTINUATION_LEAD_SOURCE
OTHER_KEY = reconcile_module.OTHER_KEY
AI_DEVELOPMENT_KEY = "ai-development"


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


class ReconcileCategoryTaxonomyTestCase(TestCase):
    """0052 reconciles already-migrated databases (the fixture edit only covers fresh installs)."""

    fixtures = ["required_bot_users", "default_columnset", "default_labelset", "default_kippoprojectorganizationcategory"]

    def _simulate_pre_migration_database(self) -> KippoOrganization:
        """Undo the new fixture's system-development global row and recreate the retired legacy globals so a
        new organization seeds copies of them (copy-on-create), reproducing a pre-migration database.
        """
        category = KippoProjectOrganizationCategory
        category.objects.filter(organization__isnull=True, key=SYSTEM_DEV_KEY).delete()
        for sort_order, key in enumerate(RETIRED_KEYS, start=100):
            category.objects.create(organization=None, key=key, label=f"legacy-{key}", sort_order=sort_order, is_active=True)
        manager = KippoUser.objects.get(username="github-manager")
        # creating the org fires seed_organization_project_categories (post_save) → org copies of the
        # then-active globals, including the retired ones, but not system-development.
        org = KippoOrganization.objects.create(
            name="legacy-taxonomy-org", github_organization_name="legacy-taxonomy-org", created_by=manager, updated_by=manager
        )
        return org

    def test_reconciles_existing_database(self):
        category = KippoProjectOrganizationCategory
        org = self._simulate_pre_migration_database()
        manager = KippoUser.objects.get(username="github-manager")
        columnset = ProjectColumnSet.objects.first()
        upsell_copy = category.objects.get(organization=org, key=UPSELL_KEYS[0])
        ai_copy = category.objects.get(organization=org, key=AI_DEVELOPMENT_KEY)
        parent = KippoProject.objects.create(
            organization=org, name="parent-proj", category=ai_copy, columnset=columnset, created_by=manager, updated_by=manager
        )
        child = KippoProject.objects.create(
            organization=org, name="child-proj", category=ai_copy, columnset=columnset, parent_project=parent, created_by=manager, updated_by=manager
        )
        # force the child onto the retired upsell category (bypass KippoProject.save() category logic)
        KippoProject.objects.filter(id=child.id).update(category=upsell_copy)

        # a second child whose parent sits on a RETIRED (non-upsell) category must fall back to その他,
        # not inherit the soon-deactivated retired category.
        retired_copy = category.objects.get(organization=org, key=RETIRED_KEYS[0])
        other_copy = category.objects.get(organization=org, key=OTHER_KEY)
        parent_on_retired = KippoProject.objects.create(
            organization=org, name="parent-retired", category=retired_copy, columnset=columnset, created_by=manager, updated_by=manager
        )
        child2 = KippoProject.objects.create(
            organization=org,
            name="child2-proj",
            category=ai_copy,
            columnset=columnset,
            parent_project=parent_on_retired,
            created_by=manager,
            updated_by=manager,
        )
        KippoProject.objects.filter(id=child2.id).update(category=upsell_copy)

        reconcile(django_apps, schema_editor=None)

        child.refresh_from_db()
        # the upsell-categorized child is stamped 継続 and repointed to its parent's category
        self.assertEqual(child.lead_source, CONTINUATION_LEAD_SOURCE)
        self.assertEqual(child.category_id, ai_copy.id)
        # child2's parent was on a retired category → child2 falls back to その他, not the retired row
        child2.refresh_from_db()
        self.assertEqual(child2.lead_source, CONTINUATION_LEAD_SOURCE)
        self.assertEqual(child2.category_id, other_copy.id)
        # system-development is seeded globally and per-org, active
        self.assertTrue(category.objects.filter(organization__isnull=True, key=SYSTEM_DEV_KEY, is_active=True).exists())
        self.assertTrue(category.objects.filter(organization=org, key=SYSTEM_DEV_KEY, is_active=True).exists())
        # every retired category (global + org copies) is deactivated, not deleted
        self.assertFalse(category.objects.filter(key__in=RETIRED_KEYS, is_active=True).exists())
        self.assertTrue(category.objects.filter(organization=org, key=UPSELL_KEYS[0]).exists())

    def test_idempotent_on_fresh_database(self):
        category = KippoProjectOrganizationCategory

        reconcile(django_apps, schema_editor=None)

        self.assertEqual(category.objects.filter(organization__isnull=True, key=SYSTEM_DEV_KEY).count(), 1)
        self.assertFalse(category.objects.filter(key__in=RETIRED_KEYS).exists())
