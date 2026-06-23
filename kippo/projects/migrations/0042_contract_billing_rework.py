# kippo#31 follow-up: rework the contract/billing model.
# Contract: rename `amount` -> `total_amount` (the whole-contract total; monthly is split across
# months) and make the project relation OneToOne (one contract per project).
# BillingEntry: anchor it on the contract (the unit of account) instead of the project — drop the
# project FK, require the contract FK, add `is_manual`, and move per-date uniqueness onto the
# contract. No data conversion: no contract/billing rows exist yet.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0041_alter_projectmonthlyassignment_project_and_more"),
    ]

    operations = [
        # --- KippoProjectContract: total_amount + OneToOne project ---
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
        # --- KippoProjectBillingEntry: anchor on the contract ---
        migrations.RemoveConstraint(
            model_name="kippoprojectbillingentry",
            name="unique_billingentry_project_billing_date",
        ),
        migrations.AddField(
            model_name="kippoprojectbillingentry",
            name="is_manual",
            field=models.BooleanField(
                default=False,
                help_text="True for hand-added entries; False for entries generated from the contract terms.",
                verbose_name="手動追加",
            ),
        ),
        migrations.AlterField(
            model_name="kippoprojectbillingentry",
            name="contract",
            field=models.ForeignKey(
                help_text="Contract this billing entry belongs to.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="billing_entries",
                to="projects.kippoprojectcontract",
            ),
        ),
        migrations.RemoveField(
            model_name="kippoprojectbillingentry",
            name="project",
        ),
        migrations.AddConstraint(
            model_name="kippoprojectbillingentry",
            constraint=models.UniqueConstraint(
                fields=("contract", "billing_date"),
                name="unique_billingentry_contract_billing_date",
            ),
        ),
    ]
