"""
Update KippoTasks from github issues defined github orginazational projects.

Can be run via zappa with the command:

    zappa manage dev "update_github_tasks -o {MY ORG} -d 2018-3-14"
"""
from django.utils import timezone
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import ugettext as _

from accounts.models import KippoOrganization
from ...periodic.tasks import collect_github_project_issues


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('-g', '--github-organization-name',
                            type=str,
                            default=None,
                            required=True,
                            help=_('KippoOrganization to retrieve Github Information for.'))
        parser.add_argument('-d', '--date',
                            type=str,
                            default=None,
                            help=_('Date to run update for in YYYY-MM-DD format'))

    def handle(self, *args, **options):

        github_organization_name = options['github_organization_name']
        try:
            organization = KippoOrganization.objects.get(github_organization_name=github_organization_name)
        except KippoOrganization.DoesNotExist:
            raise CommandError(f'Given "--github-organization-name" does not exist in registered KippoOrganizations: {github_organization_name}')

        status_effort_date = timezone.now().date()
        if options['date']:
            try:
                status_effort_date = timezone.datetime.strptime('2018-1-14', '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('Invalid value given for -d/--date option, should be in YYYY-MM-DD format: {}'.format(options['date']))

        self.stdout.write(f'Update Started for {options["github_organization_name"]} ({status_effort_date})!\n')
        processed_projects, new_task_count, new_taskstatus_count, updated_taskstatus_count = collect_github_project_issues(kippo_organization=organization,
                                                                                                                           status_effort_date=status_effort_date)
        self.stdout.write('Update Complete!')
        self.stdout.write(f'\tProjects Processed     : {processed_projects}')
        self.stdout.write(f'\tNew KippoTask(s)       : {new_task_count}')
        self.stdout.write(f'\tNew KippoTaskStatus    : {new_taskstatus_count}')
        self.stdout.write(f'\tUpdated KippoTaskStatus: {updated_taskstatus_count}\n')
