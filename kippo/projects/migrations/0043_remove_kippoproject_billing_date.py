# Remove the vestigial KippoProject.billing_date (added in #279, pre-dating the contract/ledger
# model). Billing dates now live on KippoProjectBillingEntry.billing_date (per entry) and the
# contract period drives delivery billing — the project-level field was unused by any logic.
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0042_contract_billing_rework"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="kippoproject",
            name="billing_date",
        ),
    ]
