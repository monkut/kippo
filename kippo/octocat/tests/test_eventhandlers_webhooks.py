import datetime
import json
import os
from pathlib import Path

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from tasks.models import KippoTask, KippoTaskStatus

from ..event_handlers.webhooks import process_webhooks
from ..models import GithubWebhookEvent
from .utils import load_webhookevent

assert os.getenv("KIPPO_TESTING", None)  # The KIPPO_TESTING environment variable must be set to True

TESTDATA_DIRECTORY = Path(__file__).parent / "testdata"


class ProcessWebhooksTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self) -> None:
        self.repository_name = "myrepo"
        results = setup_basic_project(repository_name=self.repository_name)

        self.organization = results["KippoOrganization"]
        self.secret_encoded = self.organization.webhook_secret.encode("utf8")
        self.project = results["KippoProject"]
        self.user1 = results["KippoUser"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        self.secret = "DOB6tzKvmBIX69Jd1NPc"  # noqa: S105
        self.secret_encoded = self.secret.encode("utf8")

        # create user2 for task assignement check
        self.user2 = KippoUser(username="octocat2", github_login="octocat2", password="test", email="octocat2@github.com", is_staff=True)  # noqa: S106
        self.user2.save()

        orgmembership = OrganizationMembership(
            user=self.user2,
            organization=self.organization,
            is_developer=True,
            created_by=self.user2,
            updated_by=self.user2,
        )
        orgmembership.save()
        self.current_date = timezone.now().date()

        # remove existing task/taskstatus
        KippoTaskStatus.objects.all().delete()
        KippoTask.objects.all().delete()

        # create existing task
        existing_task = KippoTask(
            title="kippo task title",
            project=self.project,
            assignee=self.user1,
            description="body",
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository_name}/issues/9",
            github_issue_html_url=f"https://github.com/{self.organization.github_organization_name}/{self.repository_name}/issues/9",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_task.save()

        # create existing taskstatus
        existing_taskstatus = KippoTaskStatus(
            task=existing_task,
            state="open",
            effort_date=self.current_date,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_taskstatus.save()

        # create GithubWebhookEvent
        event_filepath = TESTDATA_DIRECTORY / "issues_webhook_existing.json"
        event_encoded, _ = load_webhookevent(event_filepath, secret_encoded=self.secret_encoded)
        event = json.loads(event_encoded.decode("utf8"))
        event_type = "issues"
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type=event_type, event=event)
        webhookevent.save()

        # create old processed event
        delete_datetime = timezone.now() - datetime.timedelta(settings.WEBHOOK_DELETE_DAYS)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type=event_type, event=event)
        webhookevent.save()
        webhookevent.created_datetime = delete_datetime
        webhookevent.save()
        webhookevent.refresh_from_db()
        assert webhookevent.created_datetime == delete_datetime, webhookevent.created_datetime

        webhookevent = GithubWebhookEvent(organization=self.organization, state="processed", event_type=event_type, event=event)
        webhookevent.save()
        webhookevent.created_datetime = delete_datetime
        webhookevent.save()
        webhookevent.refresh_from_db()
        assert webhookevent.created_datetime == delete_datetime, webhookevent.created_datetime

    def test_process_webhooks(self):
        initial_event_count = 3
        assert GithubWebhookEvent.objects.all().count() == initial_event_count
        process_webhooks(event={}, context={})
        expected_event_count = 1
        self.assertEqual(GithubWebhookEvent.objects.all().count(), expected_event_count)
