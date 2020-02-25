import hashlib
import hmac
import os
from http import HTTPStatus
from pathlib import Path
from typing import Tuple

from common.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import Client, TestCase

from ..models import GithubWebhookEvent
from .utils import load_webhookevent

assert os.getenv("KIPPO_TESTING", False)  # The KIPPO_TESTING environment variable must be set to True
TESTDATA_DIRECTORY = Path(__file__).parent / "testdata"


class OctocatViewsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.client = Client()
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.secret_encoded = self.organization.webhook_secret.encode("utf8")
        GithubWebhookEvent.objects.all().delete()

    def test_application_xwwwformurlencoded(self):
        event_filepath = TESTDATA_DIRECTORY / "issues_webhook_edited.payload"
        content, signature = load_webhookevent(event_filepath, secret_encoded=self.secret_encoded)

        headers = {"X-Github-Event": "issues", "X-Hub-Signature": signature}

        response = self.client.generic(
            "POST", self.organization.webhook_url, content, content_type="application/x-www-form-urlencoded", follow=True, **headers
        )
        expected = HTTPStatus.NO_CONTENT
        actual = response.status_code
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        # confirm that GithubWebhookEvent is created
        self.assertTrue(GithubWebhookEvent.objects.count() == 1)

    def test_application_json(self):
        event_filepath = TESTDATA_DIRECTORY / "issues_webhook_edited.json"
        content, signature = load_webhookevent(event_filepath, secret_encoded=self.secret_encoded)

        headers = {"X-Github-Event": "issues", "X-Hub-Signature": signature}
        response = self.client.generic("POST", self.organization.webhook_url, content, content_type="application/json", follow=True, **headers)
        expected = HTTPStatus.NO_CONTENT
        actual = response.status_code
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected}): {response.content}")

        # confirm that GithubWebhookEvent is created
        self.assertTrue(GithubWebhookEvent.objects.count() == 1)

    def test_invalid_contenttype(self):
        event_filepath = TESTDATA_DIRECTORY / "issues_webhook_edited.payload"
        content, signature = load_webhookevent(event_filepath, secret_encoded=self.secret_encoded)

        headers = {"X-Github-Event": "issues", "X-Hub-Signature": signature}
        response = self.client.generic("POST", self.organization.webhook_url, content, content_type="text/html", follow=True, **headers)
        expected = HTTPStatus.BAD_REQUEST
        actual = response.status_code
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        # confirm that GithubWebhookEvent is created
        self.assertTrue(GithubWebhookEvent.objects.count() == 0)
