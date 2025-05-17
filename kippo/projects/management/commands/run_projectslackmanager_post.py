"""
Collect existing github organizational projects and create the related KippoProject objects
for task perodic task collection

Can be run via zappa with the command:

    zappa manage dev "update_github_tasks --github-organization-name {MY ORG GITHUB NAME}
"""

import datetime
from argparse import ArgumentParser

from accounts.models import KippoOrganization, KippoUser
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from ...managers import ProjectSlackManager

try:
    CLI_USER = KippoUser.objects.get(username=settings.CLI_MANAGER_USERNAME)
except KippoUser.DoesNotExist as e:
    raise CommandError(f"Expected user not created: {settings.CLI_MANAGER_USERNAME}") from e


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: ArgumentParser):
        parser.add_argument(
            "-o",
            "--github-organization-name",
            type=str,
            default=None,
            required=True,
            help=_("KippoOrganization to retrieve Github Information for."),
        )
        parser.add_argument(
            "-g",
            "--week-start",
            type=str,
            default=None,
            required=False,
            help=_("Start date of the week to collect projects for."),
        )

    def handle(self, *args, **options):
        github_organization_name = options["github_organization_name"]
        try:
            organization = KippoOrganization.objects.get(github_organization_name=github_organization_name)
        except KippoOrganization.DoesNotExist as e:
            raise CommandError(
                f'Given "--github-organization-name" does not exist in registered KippoOrganizations: {github_organization_name}'
            ) from e

        week_start = None
        week_start_str = options["week_start"]
        if week_start_str:
            try:
                week_start = datetime.datetime.strptime(week_start_str, "%Y-%m-%d").date()  # noqa: DTZ007
            except ValueError as e:
                raise CommandError(f'Given "--week-start" is not a valid date format (YYYY-MM-DD): {week_start_str}') from e

        mgr = ProjectSlackManager(organization=organization)
        mgr.post_weekly_project_status(week_start_date=week_start)
