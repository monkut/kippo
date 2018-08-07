"""
Collect existing github organizational projects and create the related KippoProject objects
for task perodic task collection

Can be run via zappa with the command:

    zappa manage dev "update_github_tasks --github-organization-name {MY ORG GITHUB NAME}
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import ugettext as _
from django.conf import settings
from ...functions import collect_existing_github_projects
from accounts.models import KippoOrganization, KippoUser

try:
    CLI_USER = KippoUser.objects.get(username=settings.CLI_MANAGER_USERNAME)
except KippoUser.DoesNotExist:
    raise CommandError(f'Expected user not created: {settings.CLI_MANAGER_USERNAME}')


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('-g', '--github-organization-name',
                            type=str,
                            default=None,
                            required=True,
                            help=_('KippoOrganization to retrieve Github Information for.'))

    def handle(self, *args, **options):
        github_organization_name = options['github_organization_name']
        try:
            organization = KippoOrganization.objects.get(github_organization_name=github_organization_name)
        except KippoOrganization.DoesNotExist:
            raise CommandError(f'Given "--github-organization-name" does not exist in registered KippoOrganizations: {github_organization_name}')

        added_projects = collect_existing_github_projects(organization, as_user=CLI_USER)
        self.stdout.write(f'({len(added_projects)}) KippoProject object(s) created!')
