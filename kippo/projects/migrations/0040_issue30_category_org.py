# kippo#30 (T08, T20): introduce KippoProjectOrganizationCategory and seed the global (organization=null) defaults.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# (key, label, sort_order) — kept in sync with projects.definitions.DEFAULT_KIPPOPROJECT_CATEGORIES.
# Inlined here so the historical migration is independent of future edits to that constant.
DEFAULT_CATEGORIES = [
    ("ai-development", "AI開発", 10),
    ("mathematical-optimization", "数理最適化", 20),
    ("si", "SI", 30),
    ("consulting", "コンサルティング", 40),
    ("advisory", "アドバイザリー", 50),
    ("other", "その他", 60),
    ("non-project", "非案件", 70),
    ("upsell-improvement", "(Upsell) 追加改善・拡張", 80),
    ("upsell-new-proposal", "(Upsell) 新規提案", 90),
    ("upsell-new-department", "(Upsell) 別部署紹介", 100),
]


def global_category_pk(key):
    # Deterministic PK so fixtures/tests can reference a seeded global category without a runtime lookup.
    return uuid.uuid5(uuid.NAMESPACE_URL, f"kippo://projectcategory/global/{key}")


def seed_global_categories(apps, schema_editor):
    Category = apps.get_model("projects", "KippoProjectOrganizationCategory")  # noqa: N806
    for key, label, sort_order in DEFAULT_CATEGORIES:
        Category.objects.get_or_create(
            organization=None,
            key=key,
            defaults={"id": global_category_pk(key), "label": label, "sort_order": sort_order},
        )


def unseed_global_categories(apps, schema_editor):
    Category = apps.get_model("projects", "KippoProjectOrganizationCategory")  # noqa: N806
    Category.objects.filter(organization__isnull=True, key__in=[key for key, _label, _sort in DEFAULT_CATEGORIES]).delete()


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
        migrations.RunPython(seed_global_categories, unseed_global_categories),
    ]
