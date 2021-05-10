# Generated by Django 2.2.16 on 2021-05-10 00:18

import django.db.models.deletion
import projects.functions
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0010_auto_20200330_1202"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectWeeklyEffort",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_datetime", models.DateTimeField(auto_now_add=True)),
                ("updated_datetime", models.DateTimeField(auto_now=True)),
                ("closed_datetime", models.DateTimeField(editable=False, null=True)),
                ("week_start", models.DateField(default=projects.functions.previous_week_startdate, help_text="Effort Week Start (MONDAY)")),
                (
                    "percentage",
                    models.SmallIntegerField(
                        help_text="Actual workload percentage assigned to project from available workload available for project organization"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="projects_projectweeklyeffort_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING, related_name="projectweeklyeffort_project", to="projects.KippoProject"
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="projects_projectweeklyeffort_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING, related_name="projectweeklyeffort_user", to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
    ]
