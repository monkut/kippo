import importlib

from accounts.models import KippoOrganization, KippoUser
from django.apps import apps as django_apps
from django.test import TestCase

from projects.models import KippoProjectOrganizationCategory

migration_module = importlib.import_module("projects.migrations.0031_kippoproject_category_choices_close_fields")
map_invalid_categories_to_other = migration_module.map_invalid_categories_to_other

retire_upsell_module = importlib.import_module("projects.migrations.0052_retire_upsell_seed_continuation")
retire_upsell_seed_continuation = retire_upsell_module.retire_upsell_seed_continuation
GLOBAL_CONTINUATION_PK = retire_upsell_module.GLOBAL_CONTINUATION_PK
UPSELL_KEYS = retire_upsell_module.UPSELL_KEYS

_LEGACY_UPSELL_ROWS = (
    ("upsell-improvement", "(Upsell) 追加改善・拡張", 80),
    ("upsell-new-proposal", "(Upsell) 新規提案", 90),
    ("upsell-new-department", "(Upsell) 別部署紹介", 100),
)


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


class RetireUpsellSeedContinuationTestCase(TestCase):
    """0052 reconciles already-migrated databases (the fixture edit only covers fresh installs)."""

    fixtures = ["required_bot_users", "default_columnset", "default_labelset", "default_kippoprojectorganizationcategory"]

    def _simulate_pre_migration_database(self) -> KippoOrganization:
        """Undo the new fixture's global continuation row and recreate the legacy upsell rows (global +
        an org's copies) so the DB looks like one migrated before this change.
        """
        category = KippoProjectOrganizationCategory
        category.objects.filter(organization__isnull=True, key="continuation").delete()
        # legacy global rows — upsell-improvement reuses the shared fixture pk (the slot 0052 converts).
        imp_key, imp_label, imp_order = _LEGACY_UPSELL_ROWS[0]
        category.objects.create(id=GLOBAL_CONTINUATION_PK, organization=None, key=imp_key, label=imp_label, sort_order=imp_order)
        for key, label, order in _LEGACY_UPSELL_ROWS[1:]:
            category.objects.create(organization=None, key=key, label=label, sort_order=order)
        manager = KippoUser.objects.get(username="github-manager")
        # Creating the org fires seed_organization_project_categories (post_save), which copies the
        # then-active globals — including the 3 upsell rows above — into org-scoped copies, exactly as
        # a real organization created before this change would have them. No continuation copy exists
        # (the global continuation row was deleted above).
        org = KippoOrganization.objects.create(
            name="legacy-upsell-org", github_organization_name="legacy-upsell-org", created_by=manager, updated_by=manager
        )
        assert category.objects.filter(organization=org, key__in=UPSELL_KEYS).count() == len(UPSELL_KEYS)
        assert not category.objects.filter(organization=org, key="continuation").exists()
        return org

    def test_reconciles_existing_database(self):
        org = self._simulate_pre_migration_database()
        category = KippoProjectOrganizationCategory

        retire_upsell_seed_continuation(django_apps, schema_editor=None)

        # the global upsell-improvement slot is converted in-place to an active continuation row
        global_continuation = category.objects.get(organization__isnull=True, key="continuation")
        self.assertEqual(global_continuation.pk, GLOBAL_CONTINUATION_PK)
        self.assertTrue(global_continuation.is_active)
        # the org gains its own active continuation copy
        self.assertTrue(category.objects.filter(organization=org, key="continuation", is_active=True).exists())
        # every upsell row (global leftovers + org copies) is deactivated, not deleted
        self.assertFalse(category.objects.filter(key__in=UPSELL_KEYS, is_active=True).exists())
        self.assertEqual(category.objects.filter(organization=org, key__in=UPSELL_KEYS).count(), 3)

    def test_idempotent_on_fresh_database(self):
        # a fresh DB already carries the global continuation row (from the fixture) and no upsell rows
        category = KippoProjectOrganizationCategory

        retire_upsell_seed_continuation(django_apps, schema_editor=None)

        self.assertEqual(category.objects.filter(organization__isnull=True, key="continuation").count(), 1)
        self.assertFalse(category.objects.filter(key__in=UPSELL_KEYS).exists())
