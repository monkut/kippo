import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only counterpart to customers.0001_initial.

    Repoints KippoProject.customer at customers.KippoCustomer and removes the
    projects.KippoCustomer model from the migration state. The physical table
    rename is performed in customers.0001_initial, so there are no database
    operations here.
    """

    dependencies = [
        ("projects", "0038_alter_projectweeklyeffort_hours"),
        ("customers", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="kippoproject",
                    name="customer",
                    field=models.ForeignKey(
                        blank=True,
                        help_text="Customer this project is delivered for (optional)",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="projects",
                        to="customers.kippocustomer",
                        verbose_name="顧客",
                    ),
                ),
                migrations.DeleteModel(name="KippoCustomer"),
            ],
            database_operations=[],
        ),
    ]
