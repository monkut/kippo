"""Retire the 3 upsell project categories in favor of a single 継続 (continuation) category.

The fixture edit (default_kippoprojectorganizationcategory.json) only covers fresh installs — it is
replayed by 0040_issue33_pending_consolidated.load_default_categories, which already ran on existing
deployments and will not re-run. This RunPython reconciles already-migrated databases:

  1. Ensure the global (organization=null) `continuation` row exists. On an existing DB this converts
     the old global `upsell-improvement` row (same fixture pk, seeded by 0040) into `continuation`; on
     a fresh DB the fixture already created that row → idempotent no-op.
  2. Seed a `continuation` copy for every organization that still has an upsell category (copy-on-create
     parity, kippo#49), so the close→continuation admin wizard's follow-up is selectable per org.
  3. Deactivate every remaining upsell-* row (global leftovers + org copies). Deactivate rather than
     delete to avoid a PROTECT error on any project still referencing one, and to stay reversible.

No project references a global category after 0048 (all were remapped to org copies), so converting the
global row in step 1 recategorizes nothing.
"""

import uuid

from django.db import migrations

UPSELL_KEYS = ("upsell-improvement", "upsell-new-proposal", "upsell-new-department")
CONTINUATION_KEY = "continuation"
CONTINUATION_LABEL = "継続"
CONTINUATION_SORT_ORDER = 80
# Matches the `continuation` pk in default_kippoprojectorganizationcategory.json (the former
# `upsell-improvement` global slot) so fresh and existing databases converge on the same row.
GLOBAL_CONTINUATION_PK = uuid.UUID("231a96f4-c247-5f58-90ca-ab0f0445037a")


def retire_upsell_seed_continuation(apps, schema_editor):
    Category = apps.get_model("projects", "KippoProjectOrganizationCategory")  # noqa: N806

    # 1. Global continuation row (convert the old global upsell-improvement slot; no-op on fresh DBs).
    Category.objects.update_or_create(
        id=GLOBAL_CONTINUATION_PK,
        defaults={
            "organization_id": None,
            "key": CONTINUATION_KEY,
            "label": CONTINUATION_LABEL,
            "sort_order": CONTINUATION_SORT_ORDER,
            "is_active": True,
        },
    )

    # 2. Per-org continuation copy for every org that still has an upsell category.
    org_ids_with_upsell = set(
        Category.objects.filter(organization__isnull=False, key__in=UPSELL_KEYS).values_list("organization_id", flat=True)
    )
    for organization_id in org_ids_with_upsell:
        if not Category.objects.filter(organization_id=organization_id, key=CONTINUATION_KEY).exists():
            Category.objects.create(
                organization_id=organization_id,
                key=CONTINUATION_KEY,
                label=CONTINUATION_LABEL,
                sort_order=CONTINUATION_SORT_ORDER,
                is_active=True,
            )

    # 3. Deactivate every remaining upsell-* row (the step-1 conversion changed the global slot's key
    #    to "continuation", so it is not matched here).
    Category.objects.filter(key__in=UPSELL_KEYS).update(is_active=False)


def reverse(apps, schema_editor):
    # Best-effort reverse: reactivate the retired upsell rows. The seeded continuation rows are left in
    # place (harmless) and the converted global slot is not restored to upsell-improvement.
    Category = apps.get_model("projects", "KippoProjectOrganizationCategory")  # noqa: N806
    Category.objects.filter(key__in=UPSELL_KEYS).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0051_month_amount_and_continuation_rename"),
    ]

    operations = [
        migrations.RunPython(retire_upsell_seed_continuation, reverse),
    ]
