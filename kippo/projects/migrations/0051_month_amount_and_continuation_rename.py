# Rename 仮月額 → 月額 (estimated_monthly_amount verbose_name) and retire the "upsell" naming on
# parent_project (help_text + related_name) in favor of "continuation" (継続). No DB schema change —
# these operations only update field metadata (verbose_name / help_text / reverse accessor name).
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0050_saleskippoproject"),
    ]

    operations = [
        migrations.AlterField(
            model_name="kippoproject",
            name="parent_project",
            field=models.ForeignKey(
                blank=True,
                help_text="継続プロジェクトの元（親）プロジェクト",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="continuation_children",
                to="projects.kippoproject",
                verbose_name="親プロジェクト",
            ),
        ),
        migrations.AlterField(
            model_name="kippoprojectcontract",
            name="estimated_monthly_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=0,
                help_text=(
                    "JPY. Provisional (仮) monthly amount for effort + monthly contracts (kippo#46): every "
                    "contract month is billed this amount up front, then corrected to actuals (実績) via the "
                    "true-up admin action before the entry is received. Blank bills logged actuals directly."
                ),
                max_digits=12,
                null=True,
                verbose_name="月額",
            ),
        ),
    ]
