# kippo#31 follow-up: anchor KippoProjectBillingEntry on the contract (the unit of account)
# instead of the project. Drop the project FK, make the contract FK required, add is_manual to
# mark hand-added entries, and move the per-date uniqueness onto the contract. No data conversion:
# no billing rows exist yet.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0042_contract_total_amount_onetoone"),
    ]

    operations = [
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
