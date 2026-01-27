import os
from http import HTTPStatus
from pathlib import Path

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import Client, TestCase

from ..functions import queue_incoming_webhook_event
from ..models import GithubWebhookEvent
from .utils import load_webhookevent

assert os.getenv("KIPPO_TESTING", None)  # The KIPPO_TESTING environment variable must be set to True
TESTDATA_DIRECTORY = Path(__file__).parent / "testdata"


class WebhookTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created_objects = setup_basic_project()
        self.organization = created_objects["KippoOrganization"]
        self.secret = "DOB6tzKvmBIX69Jd1NPc"  # noqa: S105
        self.secret_encoded = self.secret.encode("utf8")
        self.organization.github_webhook_secret = self.secret
        self.organization.save()
        GithubWebhookEvent.objects.all().delete()

    def test_webhook_ping_event(self):
        c = Client()
        webhookevent_filepath = TESTDATA_DIRECTORY / "webhookevent_ping.json"
        content, signature = load_webhookevent(webhookevent_filepath, secret_encoded=self.secret_encoded)
        headers = {"X-Github-Event": "ping", "X-Hub-Signature": signature}

        response = c.generic(
            "POST",
            f"{settings.URL_PREFIX}/octocat/webhook/{self.organization.pk}/",
            content,
            content_type="application/json",
            follow=True,
            **headers,
        )
        self.assertTrue(response.status_code == HTTPStatus.OK, f"actual({response.status_code}) != expected({HTTPStatus.OK})")

    def test_queue_incoming_webhook_event__issues(self):
        """Test that issues webhook events are queued correctly."""
        event = {"action": "opened", "issue": {"number": 1, "title": "Test issue"}}
        prepared_webhookevent = queue_incoming_webhook_event(self.organization, event_type="issues", event=event)
        self.assertTrue(prepared_webhookevent)
        self.assertEqual(prepared_webhookevent.event_type, "issues")
        self.assertEqual(prepared_webhookevent.state, "unprocessed")
