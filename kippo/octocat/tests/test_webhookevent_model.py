import json
import os
import urllib.parse
from http import HTTPStatus
from pathlib import Path

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import Client, TestCase

from ..functions import queue_incoming_project_card_event
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
        self.organization.webhook_secret = self.secret
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

    def test_project_card_webhook_valid_signature(self):
        c = Client()
        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / "project_card_asissue_webhookevent_created.json"
        content, signature = load_webhookevent(project_card_asissue_webhook_event_filepath, secret_encoded=self.secret_encoded)

        headers = {"HTTP_X_GITHUB_EVENT": "project_card", "X-Hub-Signature": signature}
        response = c.generic(
            "POST",
            f"{settings.URL_PREFIX}/octocat/webhook/{self.organization.pk}/",
            content,
            content_type="application/json",
            follow=True,
            **headers,
        )
        self.assertTrue(
            response.status_code == HTTPStatus.CREATED,
            f"actual({response.status_code}) != expected({HTTPStatus.CREATED})",
        )

        # confirm webhookevent is created
        expected_event_count = 1
        self.assertEqual(GithubWebhookEvent.objects.count(), expected_event_count)
        webhook_events = GithubWebhookEvent.objects.all()
        for event in webhook_events:
            self.assertTrue(event.state == "unprocessed", f"actual({event.state}) != expected(unprocessed)")

    def test_project_card_webhook_invalid_signature(self):
        c = Client()
        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / "project_card_asissue_webhookevent_created.json"
        content, signature = load_webhookevent(project_card_asissue_webhook_event_filepath, secret_encoded=self.secret_encoded)
        invalid_signature = signature + "x"

        headers = {"HTTP_X_GITHUB_EVENT": "project_card", "X-Hub-Signature": invalid_signature}
        response = c.generic(
            "POST",
            f"{settings.URL_PREFIX}/octocat/webhook/{self.organization.pk}/",
            content,
            content_type="application/json",
            follow=True,
            **headers,
        )
        self.assertTrue(
            response.status_code == HTTPStatus.FORBIDDEN,
            f"actual({response.status_code}) != expected({HTTPStatus.FORBIDDEN})",
        )

    def test_project_card_webhook_no_signature(self):
        c = Client()
        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / "project_card_asissue_webhookevent_created.json"
        content, _ = load_webhookevent(project_card_asissue_webhook_event_filepath, secret_encoded=self.secret_encoded)
        headers = {"HTTP_X_GITHUB_EVENT": "project_card"}
        response = c.generic(
            "POST",
            f"{settings.URL_PREFIX}/octocat/webhook/{self.organization.pk}/",
            content,
            content_type="application/json",
            follow=True,
            **headers,
        )
        self.assertTrue(response.status_code == HTTPStatus.BAD_REQUEST)

    # created, edited, moved, converted, or deleted
    def test_queue_incoming_project_card_event__created(self):
        event_filepath = TESTDATA_DIRECTORY / "project_card_asnote_webhookevent_created.payload"
        with event_filepath.open("r", encoding="utf8") as event_in:
            unquoted_payload = urllib.parse.unquote(event_in.read())
            payload = unquoted_payload.split("payload=")[-1]
            event = json.loads(payload)

        queue_incoming_project_card_event(self.organization, event_type="project_card", event=event)
        expected_event_count = 1
        self.assertEqual(GithubWebhookEvent.objects.all().count(), expected_event_count)

    def test_queue_incoming_project_card_event__edited(self):
        event_filepath = TESTDATA_DIRECTORY / "project_card_asissue_webhookevent_edited.json"
        with event_filepath.open("r", encoding="utf8") as event_in:
            event = json.load(event_in)
        prepared_webhookevent = queue_incoming_project_card_event(self.organization, event_type="project_card", event=event)
        self.assertTrue(prepared_webhookevent)

    def test_queue_incoming_project_card_event__moved(self):
        event_filepath = TESTDATA_DIRECTORY / "project_card_asissue_webhookevent_moved.payload"
        with event_filepath.open("r", encoding="utf8") as event_in:
            unquoted_payload = urllib.parse.unquote(event_in.read())
            payload = unquoted_payload.split("payload=")[-1]
            event = json.loads(payload)
        prepared_webhookevent = queue_incoming_project_card_event(self.organization, event_type="project_card", event=event)
        self.assertTrue(prepared_webhookevent)

    def test_queue_incoming_project_card_event__converted(self):
        event_filepath = TESTDATA_DIRECTORY / "project_card_asissue_webhookevent_converted.json"
        with event_filepath.open("r", encoding="utf8") as event_in:
            event = json.load(event_in)
        prepared_webhookevent = queue_incoming_project_card_event(self.organization, event_type="project_card", event=event)
        self.assertTrue(prepared_webhookevent)

    def test_queue_incoming_project_card_event__deleted(self):
        event_filepath = TESTDATA_DIRECTORY / "project_card_asissue_webhookevent_deleted.json"
        with event_filepath.open("r", encoding="utf8") as event_in:
            event = json.load(event_in)
        prepared_webhookevent = queue_incoming_project_card_event(self.organization, event_type="project_card", event=event)
        self.assertTrue(prepared_webhookevent)
