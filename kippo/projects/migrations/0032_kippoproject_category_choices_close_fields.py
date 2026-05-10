import django.db.models.deletion
from django.db import migrations, models

NEW_CATEGORY_CHOICES = (
    ("new-proposal", "新規提案"),
    ("maintenance", "保守"),
    ("poc", "poc"),
    ("instructor", "講師"),
    ("r-and-d", "R&D"),
    ("PAO", "PAO"),
    ("upsell-improvement", "(Upsell) 追加改善・拡張"),
    ("upsell-new-proposal", "(Upsell) 新規提案"),
    ("upsell-new-department", "(Upsell) 別部署紹介"),
    ("other", "その他"),
)
NEW_CATEGORY_VALUES = tuple(value for value, _label in NEW_CATEGORY_CHOICES)


def map_invalid_categories_to_other(apps, schema_editor):
    KippoProject = apps.get_model("projects", "KippoProject")  # noqa: N806
    KippoProject.objects.exclude(category__in=NEW_CATEGORY_VALUES).update(category="other")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0031_issue9_project_external_resources"),
    ]

    operations = [
        # Map invalid existing category values to "other" BEFORE narrowing max_length to avoid truncation
        migrations.RunPython(map_invalid_categories_to_other, noop_reverse),
        migrations.AddField(
            model_name="kippoproject",
            name="close_comment",
            field=models.TextField(blank=True, default="", verbose_name="Close Comment"),
        ),
        migrations.AddField(
            model_name="kippoproject",
            name="parent_project",
            field=models.ForeignKey(
                blank=True,
                help_text="Original (parent) project for upsell projects",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="upsell_children",
                to="projects.kippoproject",
            ),
        ),
        migrations.AlterField(
            model_name="kippoproject",
            name="category",
            field=models.CharField(
                choices=NEW_CATEGORY_CHOICES,
                default="poc",
                max_length=32,
            ),
        ),
    ]
