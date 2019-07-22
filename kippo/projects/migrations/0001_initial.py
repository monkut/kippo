# Generated by Django 2.2.3 on 2019-07-22 01:00

from django.conf import settings
import django.contrib.postgres.fields
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import projects.models
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='KippoProject',
            fields=[
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=256, unique=True)),
                ('slug', models.CharField(editable=False, max_length=300, unique=True)),
                ('phase', models.CharField(choices=[('lead-evaluation', 'Lead Evaluation'), ('project-proposal', 'Project Proposal Preparation'), ('project-development', 'Project Development')], default='lead-evaluation', help_text='State or phase of the project', max_length=150)),
                ('confidence', models.PositiveSmallIntegerField(default=80, help_text='0-100, Confidence level of the project proceeding to the next phase', validators=[django.core.validators.MaxValueValidator(100), django.core.validators.MinValueValidator(0)])),
                ('category', models.CharField(default='poc', max_length=256)),
                ('is_closed', models.BooleanField(default=False, help_text='Manually set when project is complete', verbose_name='Project is Closed')),
                ('display_as_active', models.BooleanField(default=True, help_text='If True, project will be included in the ActiveKippoProject List', verbose_name='Display as Active')),
                ('github_project_url', models.URLField(blank=True, null=True, verbose_name='Github Project URL')),
                ('allocated_staff_days', models.PositiveIntegerField(blank=True, help_text='Estimated Staff Days needed for Project Completion', null=True)),
                ('start_date', models.DateField(blank=True, help_text='Date the Project requires engineering resources', null=True, verbose_name='Start Date')),
                ('target_date', models.DateField(blank=True, default=projects.models.get_target_date_default, help_text='Date the Project is planned to be completed by.', null=True, verbose_name='Target Finish Date')),
                ('actual_date', models.DateField(blank=True, help_text='The date the project was actually completed on (not the initial target)', null=True, verbose_name='Actual Completed Date')),
                ('document_url', models.URLField(blank=True, help_text='URL of where documents for the projects are maintained', null=True, verbose_name='Documentation Location URL')),
                ('problem_definition', models.TextField(blank=True, help_text='Define the problem that the project is set out to solve.', null=True, verbose_name='Project Problem Definition')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ProjectColumnSet',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=256, verbose_name='Project Column Set Name')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('label_category_prefixes', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(blank=True, max_length=10), blank=True, default=projects.models.category_prefixes_default, help_text='Github Issue Labels Category Prefixes', null=True, size=None)),
                ('label_estimate_prefixes', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(blank=True, max_length=10), blank=True, default=projects.models.estimate_prefixes_default, help_text='Github Issue Labels Estimate Prefixes', null=True, size=None)),
            ],
        ),
        migrations.CreateModel(
            name='ProjectAssignment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('percentage', models.SmallIntegerField(help_text='Workload percentage assigned to project from available workload available for project organization')),
                ('created_by', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_projectassignment_created_by', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='projectassignment_project', to='projects.KippoProject')),
                ('updated_by', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_projectassignment_updated_by', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='projectassignment_user', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='KippoProjectStatus',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('comment', models.TextField(help_text='Current Status')),
                ('created_by', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_kippoprojectstatus_created_by', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.KippoProject')),
                ('updated_by', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_kippoprojectstatus_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='kippoproject',
            name='columnset',
            field=models.ForeignKey(help_text='ProjectColumnSet to use if/when a related Github project is created through Kippo', on_delete=django.db.models.deletion.DO_NOTHING, to='projects.ProjectColumnSet'),
        ),
        migrations.AddField(
            model_name='kippoproject',
            name='created_by',
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_kippoproject_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='kippoproject',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='accounts.KippoOrganization'),
        ),
        migrations.AddField(
            model_name='kippoproject',
            name='project_manager',
            field=models.ForeignKey(blank=True, help_text='Project Manager assigned to the project', null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='kippoproject',
            name='updated_by',
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_kippoproject_updated_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.CreateModel(
            name='ActiveKippoProject',
            fields=[
            ],
            options={
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('projects.kippoproject',),
        ),
        migrations.CreateModel(
            name='ProjectColumn',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('index', models.PositiveSmallIntegerField(blank=True, default=None, help_text='Github Project Column Display Index (0 start)', unique=True, verbose_name='Column Display Index')),
                ('name', models.CharField(max_length=256, verbose_name='Project Column Display Name')),
                ('is_active', models.BooleanField(default=False, help_text='Set to True if tasks in column are considered ACTIVE')),
                ('is_done', models.BooleanField(default=False, help_text='Set to True if tasks in column are considered DONE')),
                ('columnset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.ProjectColumnSet')),
            ],
            options={
                'unique_together': {('columnset', 'name'), ('columnset', 'index')},
            },
        ),
        migrations.CreateModel(
            name='KippoMilestone',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_datetime', models.DateTimeField(auto_now_add=True)),
                ('updated_datetime', models.DateTimeField(auto_now=True)),
                ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                ('title', models.CharField(max_length=256, verbose_name='Title')),
                ('number', models.PositiveSmallIntegerField(editable=False, help_text='Internal Per Project Management Number')),
                ('allocated_staff_days', models.PositiveSmallIntegerField(blank=True, help_text='Budget Allocated Staff Days', null=True)),
                ('is_completed', models.BooleanField(default=False, verbose_name='Is Completed')),
                ('start_date', models.DateField(blank=True, default=None, help_text='Milestone Start Date', null=True, verbose_name='Start Date')),
                ('target_date', models.DateField(blank=True, default=None, help_text='Milestone Target Completion Date', null=True, verbose_name='Target Date')),
                ('actual_date', models.DateField(blank=True, default=None, help_text='Milestone Actual Completion Date', null=True, verbose_name='Actual Date')),
                ('description', models.TextField(blank=True, help_text='Describe the purpose of the milestone', null=True, verbose_name='Description')),
                ('created_by', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_kippomilestone_created_by', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, to='projects.KippoProject', verbose_name='Kippo Project')),
                ('updated_by', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='projects_kippomilestone_updated_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('project', 'start_date', 'target_date')},
            },
        ),
    ]
