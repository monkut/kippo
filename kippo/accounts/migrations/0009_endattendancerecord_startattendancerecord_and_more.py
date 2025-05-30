# Generated by Django 5.2.1 on 2025-05-19 12:19

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_remove_kippouser_slack_user_id_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='EndAttendanceRecord',
            fields=[
            ],
            options={
                'verbose_name': '退勤記録',
                'verbose_name_plural': '退勤記録',
                'ordering': ['-entry_datetime'],
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('accounts.attendancerecord',),
        ),
        migrations.CreateModel(
            name='StartAttendanceRecord',
            fields=[
            ],
            options={
                'verbose_name': '出勤記録',
                'verbose_name_plural': '出勤記録',
                'ordering': ['-entry_datetime'],
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('accounts.attendancerecord',),
        ),
        migrations.RemoveField(
            model_name='attendancerecord',
            name='user',
        ),
        migrations.AddField(
            model_name='attendancerecord',
            name='closed_datetime',
            field=models.DateTimeField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='attendancerecord',
            name='created_by',
            field=models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='attendancerecord',
            name='updated_by',
            field=models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='kippoorganization',
            name='default_holiday_country',
            field=models.ForeignKey(blank=True, help_text='Country that organization defaults to for holidays', null=True, on_delete=django.db.models.deletion.DO_NOTHING, to='accounts.country'),
        ),
        migrations.AddField(
            model_name='personalholiday',
            name='closed_datetime',
            field=models.DateTimeField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='personalholiday',
            name='created_by',
            field=models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='personalholiday',
            name='updated_by',
            field=models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='personalholiday',
            name='updated_datetime',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name='attendancerecord',
            name='entry_datetime',
            field=models.DateTimeField(default=django.utils.timezone.localtime),
        ),
    ]
