"""Copy-on-create org categories backfill (kippo#49).

For every existing organization:
  1. copy the active global (organization=null) default categories into org-scoped rows
     (idempotent by key), and
  2. remap each of the org's projects from the global category it references to the org's
     copy of the same key.

Globals are retained as the seed template (not deleted), so no PROTECT delete occurs. The
reverse repoints projects back to the global of the same key and removes the seeded org rows
that are no longer referenced.
"""

from django.db import migrations


def seed_and_remap(apps, schema_editor):
    Category = apps.get_model("projects", "KippoProjectOrganizationCategory")  # noqa: N806
    Organization = apps.get_model("accounts", "KippoOrganization")  # noqa: N806
    Project = apps.get_model("projects", "KippoProject")  # noqa: N806

    global_defaults = list(Category.objects.filter(organization__isnull=True, is_active=True))

    for organization in Organization.objects.all():
        existing_keys = set(Category.objects.filter(organization=organization).values_list("key", flat=True))
        actor_id = getattr(organization, "created_by_id", None)
        Category.objects.bulk_create(
            [
                Category(
                    organization=organization,
                    key=default.key,
                    label=default.label,
                    sort_order=default.sort_order,
                    is_active=default.is_active,
                    created_by_id=actor_id,
                    updated_by_id=actor_id,
                )
                for default in global_defaults
                if default.key not in existing_keys
            ]
        )
        org_by_key = {c.key: c for c in Category.objects.filter(organization=organization)}

        # Remap the org's projects that still reference a global category to the org's copy of the same key.
        for project in Project.objects.filter(organization=organization).select_related("category"):
            category = project.category
            if category is None or category.organization_id is not None:
                continue  # already org-scoped
            target = org_by_key.get(category.key) or org_by_key.get("other")
            if target is not None and target.pk != category.pk:
                project.category = target
                project.save(update_fields=["category"])


def unremap(apps, schema_editor):
    Category = apps.get_model("projects", "KippoProjectOrganizationCategory")  # noqa: N806
    Project = apps.get_model("projects", "KippoProject")  # noqa: N806

    globals_by_key = {c.key: c for c in Category.objects.filter(organization__isnull=True)}

    # Repoint projects back to the matching global, then drop the seeded (now-unreferenced) org rows.
    for project in Project.objects.select_related("category").all():
        category = project.category
        if category is None or category.organization_id is None:
            continue
        target = globals_by_key.get(category.key)
        if target is not None and target.pk != category.pk:
            project.category = target
            project.save(update_fields=["category"])

    Category.objects.filter(organization__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0047_kippoprojectcontract_estimated_monthly_amount_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_and_remap, unremap),
    ]
