"""
Script to load from old db to the new (2019-7-26) db structure
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import ugettext as _
from django.conf import settings

import psycopg2
import psycopg2.extras

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from projects.models import KippoProject, KippoProjectStatus, ProjectColumnSet
from octocat.models import GithubRepository, GithubRepositoryLabelSet
from tasks.models import KippoTask, KippoTaskStatus

try:
    CLI_USER = KippoUser.objects.get(username=settings.CLI_MANAGER_USERNAME)
except KippoUser.DoesNotExist:
    raise CommandError(f'Expected user not created: {settings.CLI_MANAGER_USERNAME}')


ADMIN_USER = KippoUser.objects.get(username='admin')
GITHUB_USER = KippoUser.objects.get(username='github-manager')
DEFAULT_LABELSET = GithubRepositoryLabelSet.objects.all()[0]
DEFAULT_COLUMNSET = ProjectColumnSet.objects.all()[0]


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('-d', '--dbname',
                            type=str,
                            default=None,
                            required=True,
                            help=_('Database name to migrate data from')
        )
        parser.add_argument(
            '--host',
            default='127.0.0.1',
        )
        parser.add_argument(
            '-p', '--port',
            type=int,
            default=5432,
            help=_('Database Port')
        )
        parser.add_argument(
            '-u', '--user',
            type=str,
            default='postgres'
        )
        parser.add_argument(
            '--password',
            default='mysecretpassword',
        )

    def handle(self, *args, **options):
        params = {
            'dbname': options['dbname'],
            'user': options['user'],
            'password': options['password'],
            'host': options['host'],
            'port': options['port']
        }
        with psycopg2.connect(**params) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                # load users
                table_name = 'accounts_kippouser'
                cursor.execute(f"SELECT * from {table_name} WHERE username NOT IN ('admin', 'github-manager', 'cli-manager')")
                existing_users = {u.username: u for u in KippoUser.objects.all()}
                user_previous_id = {}
                for result in cursor:
                    existing_user = existing_users.get(result['username'], None)
                    if existing_user:
                        self.stdout.write(f'Using Existing: {existing_user} ({result["id"]})')
                        user_previous_id[result['id']] = existing_user
                    else:
                        user = KippoUser(
                            is_superuser=result['is_superuser'],
                            username=result['username'],
                            first_name=result['first_name'],
                            last_name=result['last_name'],
                            is_staff=result['is_active'],
                            github_login=result['github_login'],
                            holiday_country_id=result['holiday_country_id']
                        )
                        self.stdout.write(f'Create NEW: {user} ({result["id"]})')
                        user.save()
                        user_previous_id[result['id']] = user

                # get organization(s)
                table_name = 'accounts_kippoorganization'
                cursor.execute(f"SELECT * from {table_name}")
                existing_organizations = {o.name: o for o in KippoOrganization.objects.all()}
                organization_previous_id = {}
                for result in cursor:
                    existing_organization = existing_organizations.get(result['name'], None)
                    if existing_organization:
                        self.stdout.write(f'Using Existing: {existing_organization}')
                        organization_previous_id[result['id']] = existing_organization
                    else:
                        new_organization = KippoOrganization(
                            name=result['name'],
                            github_organization_name=result['github_organization_name'],
                            default_task_category=result['default_task_category'],
                            default_task_display_state=result['default_task_display_state'],
                            day_workhours=result['day_workhours'],
                            created_datetime=result['created_datetime'],
                            updated_datetime=result['updated_datetime'],
                            created_by=ADMIN_USER,
                            updated_by=ADMIN_USER,
                        )
                        self.stdout.write(f'Create NEW: {new_organization}')
                        new_organization.save()
                        organization_previous_id[result['id']] = new_organization

                # load organization assignments
                table_name = 'accounts_organizationmembership'
                cursor.execute(f"SELECT * from {table_name}")
                for result in cursor:
                    member = user_previous_id.get(result['user_id'], None)
                    if member:
                        result = dict(result)
                        result.pop('id')

                        result['organization'] = organization_previous_id[result['organization_id']]
                        result.pop('organization_id')
                        result['user'] = user_previous_id[result['user_id']]
                        result.pop('user_id')
                        result.pop('updated_by_id')
                        result.pop('created_by_id')
                        membership = OrganizationMembership(
                            created_by=ADMIN_USER,
                            updated_by=ADMIN_USER,
                            **result
                        )
                        self.stdout.write(f'Create NEW: {membership}')
                        membership.save()

                # load projects
                table_name = 'projects_kippoproject'
                cursor.execute(f"SELECT * from {table_name}")
                previous_project_id = {}
                existing_projects = {p.name: p for p in KippoProject.objects.all()}
                for result in cursor:
                    existing_project = existing_projects.get(result['name'])
                    if existing_project:
                        self.stdout.write(f'Using existing: {existing_project}')
                        previous_project_id[result['id']] = existing_project
                    else:
                        organization = organization_previous_id[result['organization_id']]
                        project = KippoProject(
                            name=result['name'],
                            created_datetime=result['created_datetime'],
                            updated_datetime=result['updated_datetime'],
                            organization=organization,
                            columnset=DEFAULT_COLUMNSET,
                            created_by=ADMIN_USER,
                            updated_by=ADMIN_USER,
                        )
                        self.stdout.write(f'Create NEW: {project}')
                        project.save()
                        previous_project_id[result['id']] = project

                # load project status
                table_name = 'projects_kippoprojectstatus'
                cursor.execute(f"SELECT * from {table_name}")
                existing_projectstatuses = {p.comment: p for p in KippoProjectStatus.objects.all()}
                for result in cursor:
                    existing_projectstatus = existing_projectstatuses.get(result['comment'], None)
                    if not existing_projectstatus:
                        created_by_user = user_previous_id.get(result['created_by_id'], ADMIN_USER)
                        updated_by_user = user_previous_id.get(result['updated_by_id'], ADMIN_USER)
                        projectstatus = KippoProjectStatus(
                            created_datetime=result['created_datetime'],
                            updated_datetime=result['updated_datetime'],
                            created_by=created_by_user,
                            updated_by=updated_by_user,
                            project=previous_project_id[result['project_id']],
                            comment=result['comment'],
                        )
                        self.stdout.write(f'Creating NEW: {projectstatus}')
                        projectstatus.save()

                # load github repositories
                table_name = 'octocat_githubrepository'
                cursor.execute(f"SELECT * from {table_name}")
                existing_repos = {r.html_url: r for r in GithubRepository.objects.all()}
                for result in cursor:
                    existing_repo = existing_repos.get(result['html_url'])
                    if not existing_repo:
                        organization = organization_previous_id[result['organization_id']]
                        repo = GithubRepository(
                            name=result['name'],
                            api_url=result['api_url'],
                            html_url=result['html_url'],
                            label_set=DEFAULT_LABELSET,
                            created_datetime=result['created_datetime'],
                            updated_datetime=result['updated_datetime'],
                            organization=organization,
                            created_by=GITHUB_USER,
                            updated_by=GITHUB_USER,
                        )
                        self.stdout.write(f'Create NEW: {repo}')
                        repo.save()

                # load tasks
                table_name = 'tasks_kippotask'
                cursor.execute(f"SELECT * from {table_name}")
                task_previous_id = {}
                existing_tasks = {t.github_issue_html_url: t for t in KippoTask.objects.all()}
                for result in cursor:
                    existing_task = existing_tasks.get(result['github_issue_html_url'])
                    if existing_task:
                        self.stdout.write(f'Use Existing: {existing_task}')
                        task_previous_id[result['id']] = existing_task
                    else:
                        result = dict(result)
                        previous_id = result.pop('id')

                        result['project'] = previous_project_id[result['project_id']]
                        result.pop('project_id')

                        result['assignee'] = user_previous_id[result['assignee_id']]
                        result.pop('assignee_id')

                        result.pop('updated_by_id')
                        result.pop('created_by_id')
                        task = KippoTask(
                            created_by=GITHUB_USER,
                            updated_by=GITHUB_USER,
                            **result
                        )
                        self.stdout.write(f'Create NEW: {task}')
                        task.save()
                        task_previous_id[previous_id] = task

                # load task status
                table_name = 'tasks_kippotaskstatus'
                cursor.execute(f"SELECT * from {table_name}")
                exisiting_taskstatuses = {(t.effort_date, t.task_id): t for t in KippoTaskStatus.objects.all()}
                for result in cursor:
                    key = (result['effort_date'], task_previous_id[result['task_id']].id)
                    exisiting_taskstatus = exisiting_taskstatuses.get(key, None)
                    if not exisiting_taskstatus:
                        result = dict(result)
                        result.pop('id')
                        result.pop('created_by_id')
                        result.pop('updated_by_id')

                        result['task'] = task_previous_id[result['task_id']]
                        result.pop('task_id')

                        taskstatus = KippoTaskStatus(
                            created_by=GITHUB_USER,
                            updated_by=GITHUB_USER,
                            **result
                        )
                        self.stdout.write(f'Create NEW: {taskstatus}')
                        taskstatus.save()
