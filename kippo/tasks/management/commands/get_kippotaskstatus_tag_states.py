"""
Collect and output TaskStatus tag state counts for a given day.

Can be run via zappa with the command:

    zappa manage dev "get_kippotaskstatus_tag_status -o {MY ORG} -d 2018-3-14"
"""
from collections import defaultdict, Counter
from django.utils import timezone
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import ugettext as _

from ...models import KippoTaskStatus


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument(
            '-g', '--github-organization-name',
            type=str,
            default=None,
            required=True,
            help=_('KippoOrganization to retrieve Github Information for.')
        )
        parser.add_argument(
            '-d', '--date',
            type=str,
            default=None,
            help=_('Date to run update for in YYYY-MM-DD format')
        )
        parser.add_argument(
            '-t', '--tag',
            type=str,
            required=True,
            help=_('tag name to collect states for')
        )

    def handle(self, *args, **options):

        github_organization_name = options['github_organization_name']

        status_effort_date = timezone.now().date()
        if options['date']:
            try:
                status_effort_date = timezone.datetime.strptime(options['date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('Invalid value given for -d/--date option, should be in YYYY-MM-DD format: {}'.format(options['date']))

        self.stdout.write(f'Collecting states for {github_organization_name}: {options["tag"]}\n')

        statuses = KippoTaskStatus.objects.filter(
            task__project__organization__github_organization_name=github_organization_name,
            effort_date=status_effort_date
        )
        tag_values_status_count = defaultdict(Counter)
        for status in statuses:
            for tag in status.tags:
                if tag['name'] == options['tag']:
                    tag_values_status_count[tag['value']][status.state] += 1
        for name in sorted(tag_values_status_count):
            self.stdout.write(f'{name}: {tag_values_status_count[name]}')

