# Generated by Django 2.2.28 on 2022-06-10 02:05

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0014_auto_20220610_1022"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="kippoprojectuserstatisfactionresult",
            options={"verbose_name": "振り返り従業員アンケート", "verbose_name_plural": "振り返り従業員アンケート"},
        ),
        migrations.AlterField(
            model_name="kippoprojectuserstatisfactionresult",
            name="fullfillment_score",
            field=models.PositiveSmallIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], verbose_name="充実した時間"),
        ),
        migrations.AlterField(
            model_name="kippoprojectuserstatisfactionresult",
            name="growth_score",
            field=models.PositiveSmallIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], verbose_name="成長"),
        ),
        migrations.CreateModel(
            name="KippoProjectUserMonthlyStatisfactionResult",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_datetime", models.DateTimeField(auto_now_add=True)),
                ("updated_datetime", models.DateTimeField(auto_now=True)),
                ("closed_datetime", models.DateTimeField(editable=False, null=True)),
                ("fullfillment_score", models.PositiveSmallIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], verbose_name="充実した時間")),
                ("growth_score", models.PositiveSmallIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], verbose_name="成長")),
                (
                    "created_by",
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="projects_kippoprojectusermonthlystatisfactionresult_created_by",
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
                        related_name="projects_kippoprojectusermonthlystatisfactionresult_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "（月）従業員アンケート",
                "verbose_name_plural": "（月）従業員アンケート",
            },
        ),
    ]
