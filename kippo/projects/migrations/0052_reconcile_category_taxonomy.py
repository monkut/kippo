"""Reconcile the KippoProject category taxonomy on already-migrated databases.

The fixture edit (default_kippoprojectorganizationcategory.json) only covers fresh installs — it is
replayed by 0040_issue33_pending_consolidated.load_default_categories, which already ran on existing
deployments. This RunPython brings existing databases to the new taxonomy:

  1. Ensure the global (organization=null) `system-development` (システム開発) category exists.
  2. Seed a `system-development` copy for every organization that copied the default set (copy-on-create
     parity, kippo#49), so it is selectable per org.
  3. Migrate every project still categorized as a retired `upsell-*` row to the continuation model:
     set `lead_source="continuation"` and repoint `category` to the parent project's category (else the
     org's `other` / その他). Must run before step 4 removes the upsell rows from the picker.
  4. Deactivate the retired categories (mathematical-optimization, si, advisory, upsell-*) globally and
     per-org. Deactivate (not delete) so any project still referencing one keeps validating (PROTECT-safe)
     and the change stays reversible.
"""

import uuid

from django.db import migrations

UPSELL_KEYS = ("upsell-improvement", "upsell-new-proposal", "upsell-new-department")
RETIRED_KEYS = ("mathematical-optimization", "si", "advisory", *UPSELL_KEYS)
CONTINUATION_LEAD_SOURCE = "continuation"
OTHER_KEY = "other"
SYSTEM_DEV_KEY = "system-development"
SYSTEM_DEV_LABEL = "システム開発"
SYSTEM_DEV_SORT_ORDER = 20
# Matches the `system-development` pk in default_kippoprojectorganizationcategory.json.
GLOBAL_SYSTEM_DEV_PK = uuid.UUID("ea83a629-4bba-5ad8-90d6-75b1986f394e")


def _org_other_category(category_model, organization_id):
    return (
        category_model.objects.filter(organization_id=organization_id, key=OTHER_KEY).first()
        or category_model.objects.filter(organization__isnull=True, key=OTHER_KEY).first()
    )


def reconcile(apps, schema_editor):
    category_model = apps.get_model("projects", "KippoProjectOrganizationCategory")
    organization_model = apps.get_model("accounts", "KippoOrganization")
    project_model = apps.get_model("projects", "KippoProject")

    # 1. Global system-development row (no-op on fresh DBs where the fixture already created it).
    category_model.objects.update_or_create(
        id=GLOBAL_SYSTEM_DEV_PK,
        defaults={
            "organization_id": None,
            "key": SYSTEM_DEV_KEY,
            "label": SYSTEM_DEV_LABEL,
            "sort_order": SYSTEM_DEV_SORT_ORDER,
            "is_active": True,
        },
    )

    # 2. Per-org system-development copy for every org that already copied the default set.
    for organization in organization_model.objects.all():
        existing_keys = set(category_model.objects.filter(organization=organization).values_list("key", flat=True))
        if not existing_keys or SYSTEM_DEV_KEY in existing_keys:
            continue
        actor_id = getattr(organization, "created_by_id", None)
        category_model.objects.create(
            organization=organization,
            key=SYSTEM_DEV_KEY,
            label=SYSTEM_DEV_LABEL,
            sort_order=SYSTEM_DEV_SORT_ORDER,
            is_active=True,
            created_by_id=actor_id,
            updated_by_id=actor_id,
        )

    # 3. Migrate upsell-categorized projects to lead_source=継続 + a non-upsell category.
    upsell_projects = project_model.objects.filter(category__key__in=UPSELL_KEYS).select_related("category", "parent_project__category")
    for project in upsell_projects:
        parent = project.parent_project
        target = parent.category if (parent and parent.category_id and parent.category.key not in UPSELL_KEYS) else None
        if target is None:
            target = _org_other_category(category_model, project.organization_id)
        project.lead_source = CONTINUATION_LEAD_SOURCE
        update_fields = ["lead_source"]
        if target is not None and target.pk != project.category_id:
            project.category = target
            update_fields.append("category")
        project.save(update_fields=update_fields)

    # 4. Deactivate the retired categories (global + org copies).
    category_model.objects.filter(key__in=RETIRED_KEYS).update(is_active=False)


def reverse(apps, schema_editor):
    # Best-effort: reactivate the retired categories. The system-development rows, lead_source stamps, and
    # category repoints are left in place.
    category_model = apps.get_model("projects", "KippoProjectOrganizationCategory")
    category_model.objects.filter(key__in=RETIRED_KEYS).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0051_lead_source_and_continuation_parent"),
    ]

    operations = [
        migrations.RunPython(reconcile, reverse),
    ]
