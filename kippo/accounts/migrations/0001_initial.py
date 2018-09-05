# Generated by Django 2.1.1 on 2018-09-04 14:40

from django.conf import settings
import django.contrib.auth.models
import django.contrib.auth.validators
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='KippoUser',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
                ('username', models.CharField(error_messages={'unique': 'A user with that username already exists.'}, help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.', max_length=150, unique=True, validators=[django.contrib.auth.validators.UnicodeUsernameValidator()], verbose_name='username')),
                ('first_name', models.CharField(blank=True, max_length=30, verbose_name='first name')),
                ('last_name', models.CharField(blank=True, max_length=150, verbose_name='last name')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='email address')),
                ('is_staff', models.BooleanField(default=False, help_text='Designates whether the user can log into this admin site.', verbose_name='staff status')),
                ('is_active', models.BooleanField(default=True, help_text='Designates whether this user should be treated as active. Unselect this instead of deleting accounts.', verbose_name='active')),
                ('date_joined', models.DateTimeField(default=django.utils.timezone.now, verbose_name='date joined')),
                ('is_project_manager', models.BooleanField(default=False)),
                ('is_developer', models.BooleanField(default=True)),
                ('github_login', models.CharField(blank=True, default=None, help_text='Github Login username', max_length=100, null=True)),
            ],
            options={
                'verbose_name': 'user',
                'verbose_name_plural': 'users',
                'abstract': False,
            },
            managers=[
                ('objects', django.contrib.auth.models.UserManager()),
            ],
        ),
        migrations.CreateModel(
            name='EmailDomain',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('domain', models.CharField(help_text='Organization email domains allowed to access organization information [USERNAME@{DOMAIN}]', max_length=255)),
                ('is_staff_domain', models.BooleanField(default=True, help_text='Domain has access to admin')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='KippoOrganization',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('name', models.CharField(max_length=256)),
                ('github_organization_name', models.CharField(max_length=100)),
                ('day_workhours', models.PositiveSmallIntegerField(default=7, help_text='Defines the number of hours in the workday')),
                ('default_task_category', models.CharField(blank=True, default='study', help_text='Default category to apply to KippoTask objects', max_length=256, null=True)),
                ('default_task_display_state', models.CharField(default='in-progress', help_text='Default Task STATE to show on initial task view', max_length=150)),
                ('created_by', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='accounts_kippoorganization_created_by', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PersonalHoliday',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('is_half', models.BooleanField(default=False, help_text='Select if taking only a half day')),
                ('day', models.DateField()),
                ('duration', models.SmallIntegerField(default=1, help_text='How many days (including weekends/existing holidays)')),
                ('user', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-day'],
            },
        ),
    ]
