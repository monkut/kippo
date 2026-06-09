# kippo#30 (T08, T20): add KippoProjectOrganizationCategory and convert KippoProject.category to a FK.
#
# Global (organization=null) default categories are defined ONCE in the
# projects/fixtures/default_kippoprojectorganizationcategory.json fixture and loaded here from that
# file (single source of truth; the same fixture is loaded for tests via commons.tests.DEFAULT_FIXTURES
# and at deploy time via loaddata). Existing KippoProject.category strings are then remapped onto the
# new taxonomy:
#   - phase == "anon-project"  -> "non-project"   (T20: anon relocates to category; the phase value
#                                                   itself is retired later in kippo#36)
#   - value already a kept key -> same key         (upsell-*, "other")
#   - anything else            -> "other"          (no equivalent in the new taxonomy)

import json
import pathlib
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import projects.models

FIXTURE_PATH = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "default_kippoprojectorganizationcategory.json"
ANON_PROJECT_PHASE = "anon-project"
NON_PROJECT_KEY = "non-project"
FALLBACK_KEY = "other"


def load_default_categories(apps, schema_editor):
    # Load the global (organization=null) defaults from the fixture file (migration-safe: uses the
    # historical model, not call_command, so a future model change can't break replay).
    Category = apps.get_model("projects", "KippoProjectOrganizationCategory")  # noqa: N806
    entries = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for entry in entries:
        fields = entry["fields"]
        Category.objects.update_or_create(
            id=uuid.UUID(entry["pk"]),
            defaults={
                "organization_id": fields["organization"],
                "key": fields["key"],
                "label": fields["label"],
                "sort_order": fields["sort_order"],
                "is_active": fields["is_active"],
            },
        )


def unload_default_categories(apps, schema_editor):
    Category = apps.get_model("projects", "KippoProjectOrganizationCategory")  # noqa: N806
    entries = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    Category.objects.filter(id__in=[uuid.UUID(entry["pk"]) for entry in entries]).delete()


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
        ('accounts', '0016_kippoorganization_default_columnset'),
        ('projects', '0039_move_kippocustomer_to_customers_app'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='KippoProjectOrganizationCategory',
            fields=[
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('key', models.CharField(max_length=32, verbose_name='キー')),
                ('label', models.CharField(max_length=128, verbose_name='ラベル')),
                ('sort_order', models.PositiveSmallIntegerField(default=0, verbose_name='表示順')),
                ('is_active', models.BooleanField(default=True, verbose_name='有効')),
                ('created_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(blank=True, help_text='Organization this category belongs to; leave empty for a global default category', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='project_categories', to='accounts.kippoorganization', verbose_name='組織')),
                ('updated_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'プロジェクトカテゴリ',
                'verbose_name_plural': 'プロジェクトカテゴリ',
                'ordering': ('sort_order', 'key'),
            },
        ),
        migrations.AddConstraint(
            model_name='kippoprojectorganizationcategory',
            constraint=models.UniqueConstraint(fields=('organization', 'key'), name='uniq_org_category_key'),
        ),
        migrations.AddConstraint(
            model_name='kippoprojectorganizationcategory',
            constraint=models.UniqueConstraint(condition=models.Q(('organization__isnull', True)), fields=('key',), name='uniq_global_category_key'),
        ),
        migrations.RunPython(load_default_categories, unload_default_categories),
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
