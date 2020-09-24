# Generated by Django 2.2.13 on 2020-09-24 05:31

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0005_auto_20200330_1202"),
    ]

    operations = [
        migrations.AlterField(
            model_name="kippotask",
            name="milestone",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="kippotask_milestone", to="projects.KippoMilestone"
            ),
        ),
    ]
