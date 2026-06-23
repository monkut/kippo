# kippo#31 follow-up: rename KippoProjectContract.amount -> total_amount and make the
# project relation OneToOne (one contract per project). No data conversion: no rows exist yet.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0041_alter_projectmonthlyassignment_project_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="kippoprojectcontract",
            old_name="amount",
            new_name="total_amount",
        ),
        migrations.AlterField(
            model_name="kippoprojectcontract",
            name="total_amount",
            field=models.DecimalField(
                decimal_places=0,
                help_text=(
                    "JPY. Total amount for the whole contract. For 'monthly' it is split evenly across "
                    "the contract months, with the rounding remainder on the final month."
                ),
                max_digits=12,
                verbose_name="契約金額",
            ),
        ),
        migrations.AlterField(
            model_name="kippoprojectcontract",
            name="billing_type",
            field=models.CharField(
                choices=[("delivery", "納品"), ("monthly", "月額")],
                default="delivery",
                help_text=(
                    "'delivery' (納品, total_amount billed once at the contract end_date) or "
                    "'monthly' (月額, total_amount split month-end across the contract period)."
                ),
                max_length=20,
                verbose_name="請求方法",
            ),
        ),
        migrations.AlterField(
            model_name="kippoprojectcontract",
            name="project",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="contract",
                to="projects.kippoproject",
            ),
        ),
    ]
