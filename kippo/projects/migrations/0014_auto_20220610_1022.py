# Generated by Django 2.2.28 on 2022-06-10 01:22

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0013_auto_20210510_1346"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="projectweeklyeffort",
            options={"verbose_name_plural": "ProjectWeeklyEffort"},
        ),
        migrations.AlterField(
            model_name="kippoproject",
            name="phase",
            field=models.CharField(
                choices=[
                    ("anon-project", "Non-Project"),
                    ("lead-evaluation", "Lead Evaluation"),
                    ("project-proposal", "Project Proposal Preparation"),
                    ("project-development", "Project Development"),
                ],
                default="lead-evaluation",
                help_text="State or phase of the project",
                max_length=150,
            ),
        ),
        migrations.AlterField(
            model_name="projectweeklyeffort",
            name="hours",
            field=models.SmallIntegerField(help_text="Actual effort in hours performed on the project for the given 'week start'"),
        ),
        migrations.CreateModel(
            name="KippoProjectUserStatisfactionResult",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_datetime", models.DateTimeField(auto_now_add=True)),
                ("updated_datetime", models.DateTimeField(auto_now=True)),
                ("closed_datetime", models.DateTimeField(editable=False, null=True)),
                ("fullfillment_score", models.PositiveSmallIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], help_text="充実した時間")),
                ("growth_score", models.PositiveSmallIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], help_text="成長")),
                (
                    "created_by",
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="projects_kippoprojectuserstatisfactionresult_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="projects.KippoProject")),
                (
                    "updated_by",
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="projects_kippoprojectuserstatisfactionresult_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
    ]
