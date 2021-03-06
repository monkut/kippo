# Generated by Django 2.2.3 on 2019-07-31 08:56

from django.conf import settings
import django.contrib.postgres.fields.jsonb
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0002_auto_20190730_0421'),
        ('projects', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='CollectIssuesAction',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('start_datetime', models.DateTimeField()),
                ('end_datetime', models.DateTimeField()),
                ('created_by', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_collectissuesaction_created_by', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='accounts.KippoOrganization')),
                ('updated_by', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_collectissuesaction_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CollectIssuesProjectResult',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('state', models.CharField(choices=[('processing', 'processing'), ('complete', 'complete')], default='processing', max_length=10)),
                ('new_task_count', models.PositiveSmallIntegerField(default=0)),
                ('new_taskstatus_count', models.PositiveSmallIntegerField(default=0)),
                ('updated_taskstatus_count', models.PositiveSmallIntegerField(default=0)),
                ('unhandled_issues', django.contrib.postgres.fields.jsonb.JSONField()),
                ('action', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.CollectIssuesAction')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.KippoProject')),
            ],
        ),
    ]
