from django.db import migrations

NEW_CATEGORY_VALUES = (
    "new-proposal",
    "maintenance",
    "poc",
    "instructor",
    "r-and-d",
    "PAO",
    "upsell-improvement",
    "upsell-new-proposal",
    "upsell-new-department",
    "other",
)


def map_invalid_categories_to_other(apps, schema_editor):
    KippoProject = apps.get_model("projects", "KippoProject")
    KippoProject.objects.exclude(category__in=NEW_CATEGORY_VALUES).update(category="other")


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0034_alter_projectcolumnset_label_category_prefixes_and_more"),
    ]

    operations = [
        migrations.RunPython(map_invalid_categories_to_other, migrations.RunPython.noop),
    ]
