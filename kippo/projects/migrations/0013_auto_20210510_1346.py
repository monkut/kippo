# Generated by Django 2.2.22 on 2021-05-10 04:46

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0012_auto_20210510_0950"),
    ]

    operations = [
        migrations.RenameField(
            model_name="projectweeklyeffort",
            old_name="percentage",
            new_name="hours",
        ),
    ]
