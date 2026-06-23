# kippo#31 follow-up — project-level billing cleanup + payment-receipt tracking:
#   - Remove the vestigial KippoProject.billing_date (added in #279 pre-dating the contract/ledger
#     model; no logic reads it — billing dates live on KippoProjectBillingEntry.billing_date).
#   - Add is_received / received_datetime to KippoProjectBillingEntry so a billed entry can be
#     marked paid.
# Additive/removal only; no data conversion (no billing rows exist yet).
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0042_contract_billing_rework"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="kippoproject",
            name="billing_date",
        ),
        migrations.AddField(
            model_name="kippoprojectbillingentry",
            name="is_received",
            field=models.BooleanField(
                default=False,
                help_text="True once payment for this billing entry has been received.",
                verbose_name="入金済",
            ),
        ),
        migrations.AddField(
            model_name="kippoprojectbillingentry",
            name="received_datetime",
            field=models.DateTimeField(
                blank=True,
                help_text="When payment was received. Auto-set when is_received is enabled; cleared when disabled.",
                null=True,
                verbose_name="入金日時",
            ),
        ),
    ]
