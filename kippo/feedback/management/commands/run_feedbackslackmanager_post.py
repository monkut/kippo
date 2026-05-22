"""
Post the previous 7 days of feedback entries — grouped by inferred [component] — to the organization's Slack channel.

Can be run via zappa with the command:

    zappa manage prod "run_feedbackslackmanager_post --github-organization-name {MY ORG GITHUB NAME}"
"""

from argparse import ArgumentParser

from accounts.models import KippoOrganization, KippoUser
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from feedback.slackcommand.managers import FeedbackSlackManager

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
            help=_("KippoOrganization to post the weekly feedback summary for."),
        )

    def handle(self, *args, **options):
        github_organization_name = options["github_organization_name"]
        try:
            organization = KippoOrganization.objects.get(github_organization_name=github_organization_name)
        except KippoOrganization.DoesNotExist as e:
            raise CommandError(
                f'Given "--github-organization-name" does not exist in registered KippoOrganizations: {github_organization_name}'
            ) from e

        mgr = FeedbackSlackManager(organization=organization)
        mgr.post_weekly_feedback_summary()
