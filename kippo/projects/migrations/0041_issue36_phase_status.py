# kippo#36 (T09): redefine phase as the project status and derive confidence from phase.

import django.core.validators
from django.db import migrations, models

# old phase value -> new phase value. anon-project rows already carry category=="non-project" (kippo#30),
# so they only need a valid status here.
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


def remap_phase_and_backfill_confidence(apps, schema_editor):
    KippoProject = apps.get_model("projects", "KippoProject")  # noqa: N806
    for project in KippoProject.objects.all().iterator():
        new_phase = PHASE_REMAP.get(project.phase, project.phase)
        if new_phase not in PHASE_CONFIDENCE:
            new_phase = FALLBACK_PHASE
        KippoProject.objects.filter(pk=project.pk).update(phase=new_phase, confidence=PHASE_CONFIDENCE[new_phase])


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0040_kippoproject_category_organization_fk'),
    ]

    operations = [
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
        migrations.RunPython(remap_phase_and_backfill_confidence, migrations.RunPython.noop),
    ]
