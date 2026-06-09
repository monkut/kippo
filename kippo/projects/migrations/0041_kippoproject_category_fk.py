# kippo#30 (T08, T20): convert KippoProject.category (CharField) to a FK to KippoProjectOrganizationCategory.
#
# Mapping of existing category strings to the new global taxonomy:
#   - phase == "anon-project"                  -> "non-project"   (T20: anon relocates to category;
#                                                                   phase value itself is retired later in kippo#36)
#   - value already a kept global key           -> same key        (upsell-*, "other")
#   - anything else (new-proposal/maintenance/  -> "other"         (no equivalent in the new taxonomy)
#     poc/instructor/r-and-d/PAO/unknown)

import django.db.models.deletion
from django.db import migrations, models

import projects.models

ANON_PROJECT_PHASE = "anon-project"
NON_PROJECT_KEY = "non-project"
FALLBACK_KEY = "other"


def populate_category_fk(apps, schema_editor):
    KippoProject = apps.get_model("projects", "KippoProject")  # noqa: N806
    Category = apps.get_model("projects", "KippoProjectOrganizationCategory")  # noqa: N806
    globals_by_key = {c.key: c.pk for c in Category.objects.filter(organization__isnull=True)}
    fallback_pk = globals_by_key[FALLBACK_KEY]
    nonproject_pk = globals_by_key[NON_PROJECT_KEY]

    for project in KippoProject.objects.all().iterator():
        if project.phase == ANON_PROJECT_PHASE:
            target_pk = nonproject_pk
        else:
            target_pk = globals_by_key.get(project.category, fallback_pk)
        KippoProject.objects.filter(pk=project.pk).update(category_fk=target_pk)


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0040_issue30_category_org'),
    ]

    operations = [
        migrations.AddField(
            model_name='kippoproject',
            name='category_fk',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='projects',
                to='projects.kippoprojectorganizationcategory',
                verbose_name='カテゴリ',
            ),
        ),
        migrations.RunPython(populate_category_fk, migrations.RunPython.noop),
        migrations.RemoveField(model_name='kippoproject', name='category'),
        migrations.RenameField(model_name='kippoproject', old_name='category_fk', new_name='category'),
        migrations.AlterField(
            model_name='kippoproject',
            name='category',
            field=models.ForeignKey(
                default=projects.models.get_default_project_category,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='projects',
                to='projects.kippoprojectorganizationcategory',
                verbose_name='カテゴリ',
            ),
        ),
    ]
