# Squashed consolidation of the undeployed projects migrations 0040-0042 (prod was at 0039).
# The RunPython data functions are copied in verbatim from the original 0040/0041 —
# squashmigrations cannot auto-port them (kippo#30/#36 backfills + default-category seed).

import json
import pathlib
import uuid

import django.core.validators
import django.db.migrations.operations.special
import django.db.models.deletion
import projects.models
from django.conf import settings
from django.db import migrations, models

FIXTURE_PATH = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "default_kippoprojectorganizationcategory.json"
ANON_PROJECT_PHASE = "anon-project"
NON_PROJECT_KEY = "non-project"
FALLBACK_KEY = "other"

PHASE_REMAP = {
    "lead-evaluation": "proposing-low",
    "project-proposal": "proposing-high",
    "project-development": "under-contract",
    "anon-project": "under-contract",
}
PHASE_CONFIDENCE = {
    "keep-in-touch": 0,
    "proposing-low": 30,
    "proposing-mid": 80,
    "proposing-high": 90,
    "verbal-order": 99,
    "under-contract": 100,
    "completed": 100,
    "lost": 0,
}
FALLBACK_PHASE = "proposing-low"


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


def remap_phase_and_backfill_confidence(apps, schema_editor):
    KippoProject = apps.get_model("projects", "KippoProject")  # noqa: N806
    for project in KippoProject.objects.all().iterator():
        new_phase = PHASE_REMAP.get(project.phase, project.phase)
        if new_phase not in PHASE_CONFIDENCE:
            new_phase = FALLBACK_PHASE
        KippoProject.objects.filter(pk=project.pk).update(phase=new_phase, confidence=PHASE_CONFIDENCE[new_phase])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_kippoorganization_default_columnset'),
        ('accounts', '0017_kippoorganization_weekly_effort_close_offset_days'),
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
                'constraints': [models.UniqueConstraint(fields=('organization', 'key'), name='uniq_org_category_key'), models.UniqueConstraint(condition=models.Q(('organization__isnull', True)), fields=('key',), name='uniq_global_category_key')],
            },
        ),
        migrations.RunPython(
            code=load_default_categories,
            reverse_code=unload_default_categories,
        ),
        migrations.AddField(
            model_name='kippoproject',
            name='category_fk',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='projects', to='projects.kippoprojectorganizationcategory', verbose_name='カテゴリ'),
        ),
        migrations.RunPython(
            code=populate_category_fk,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name='kippoproject',
            name='category',
        ),
        migrations.RenameField(
            model_name='kippoproject',
            old_name='category_fk',
            new_name='category',
        ),
        migrations.AlterField(
            model_name='kippoproject',
            name='category',
            field=models.ForeignKey(default=projects.models.get_default_project_category, on_delete=django.db.models.deletion.PROTECT, related_name='projects', to='projects.kippoprojectorganizationcategory', verbose_name='カテゴリ'),
        ),
        migrations.AlterField(
            model_name='kippoproject',
            name='confidence',
            field=models.PositiveSmallIntegerField(default=30, editable=False, help_text='0-100, auto-derived from phase (read-only)', validators=[django.core.validators.MaxValueValidator(100), django.core.validators.MinValueValidator(0)], verbose_name='確度'),
        ),
        migrations.AlterField(
            model_name='kippoproject',
            name='phase',
            field=models.CharField(choices=[('keep-in-touch', 'KIT'), ('proposing-low', '提案(低)'), ('proposing-mid', '提案(中)'), ('proposing-high', '提案(高)'), ('verbal-order', '口頭受注'), ('under-contract', '契約稼働中'), ('completed', '完了'), ('lost', '失注')], default='proposing-low', help_text='State or phase of the project', max_length=150, verbose_name='フェーズ'),
        ),
        migrations.RunPython(
            code=remap_phase_and_backfill_confidence,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.CreateModel(
            name='KippoProjectContract',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('billing_type', models.CharField(choices=[('delivery', '納品'), ('monthly', '月額')], default='delivery', help_text="'delivery' (納品, amount billed once at the contract end_date) or 'monthly' (月額, amount accrues month-end per month).", max_length=20, verbose_name='請求方法')),
                ('amount', models.DecimalField(decimal_places=0, help_text="JPY. Contract total for 'delivery'; per-month amount for 'monthly'.", max_digits=12, verbose_name='金額')),
                ('start_date', models.DateField(blank=True, help_text='Contract period start. Auto-populated from the project start_date when left blank.', null=True, verbose_name='契約開始日')),
                ('end_date', models.DateField(blank=True, help_text='Contract period end. Auto-populated from the project target_date when left blank.', null=True, verbose_name='契約終了日')),
                ('note', models.CharField(blank=True, default='', max_length=255, verbose_name='備考')),
                ('created_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contracts', to='projects.kippoproject')),
                ('updated_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': '契約',
                'verbose_name_plural': '契約',
                'ordering': ('created_datetime',),
            },
        ),
        migrations.CreateModel(
            name='KippoProjectBillingEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('billing_date', models.DateField(help_text='Date the entry is billed/recognized. Monthly-generated entries use the month-end (月末) date.', verbose_name='請求日')),
                ('amount', models.DecimalField(decimal_places=0, help_text='Billed amount (JPY).', max_digits=12, verbose_name='金額')),
                ('note', models.CharField(blank=True, default='', max_length=255, verbose_name='備考')),
                ('created_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='billing_entries', to='projects.kippoproject')),
                ('updated_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
                ('contract', models.ForeignKey(blank=True, help_text='Contract the entry was generated from (blank for manually added entries).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='billing_entries', to='projects.kippoprojectcontract')),
            ],
            options={
                'verbose_name': '請求エントリ',
                'verbose_name_plural': '請求エントリ',
                'ordering': ('billing_date',),
                'constraints': [models.UniqueConstraint(fields=('project', 'billing_date'), name='unique_billingentry_project_billing_date')],
            },
        ),
        migrations.CreateModel(
            name='ProjectWeeklyEffortUnlock',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('week_start', models.DateField(help_text='Unlocked Effort Week Start (MONDAY)')),
                ('reason', models.TextField(help_text='アンロック申請理由 (DCAA: 締め後編集には記録された正当な理由が必要)')),
                ('approved_datetime', models.DateTimeField(blank=True, default=None, help_text='承認日時 (未設定 = 承認待ち)', null=True)),
                ('expires_datetime', models.DateTimeField(blank=True, default=None, help_text='再ロック期限: 承認時に設定され、この日時を過ぎると編集不可に戻る', null=True)),
                ('approved_by', models.ForeignKey(blank=True, default=None, help_text='承認した組織admin (未設定 = 承認待ち)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='projectweeklyeffortunlock_approved_by', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='projectweeklyeffortunlock_organization', to='accounts.kippoorganization')),
                ('updated_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='projectweeklyeffortunlock_user', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'プロジェクト週間稼働アンロック',
                'verbose_name_plural': 'プロジェクト週間稼働アンロック',
                'unique_together': {('organization', 'user', 'week_start')},
            },
        ),
    ]
