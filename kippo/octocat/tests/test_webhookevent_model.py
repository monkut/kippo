import os
import json
import hashlib
import hmac
import urllib.parse
from typing import Tuple
from pathlib import Path
from http import HTTPStatus
from django.conf import settings
from django.test import TestCase, Client

from common.tests import setup_basic_project, DEFAULT_FIXTURES
from accounts.models import KippoOrganization
from ..models import GithubWebhookEvent
from ..functions import queue_incoming_project_card_event


assert os.getenv('KIPPO_TESTING', False)  # The KIPPO_TESTING environment variable must be set to True
TESTDATA_DIRECTORY = Path(settings.BASE_DIR) / '..' / 'octocat' / 'testdata'


class WebhookTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created_objects = setup_basic_project()
        self.organization = created_objects['KippoOrganization']
        self.secret = 'DOB6tzKvmBIX69Jd1NPc'
        self.secret_encoded = self.secret.encode('utf8')
        self.organization.webhook_secret = self.secret
        self.organization.save()

    def test_webhook_ping_event(self):
        c = Client()
        webhookevent_filepath = TESTDATA_DIRECTORY / 'webhookevent_ping.json'
        with webhookevent_filepath.open('rb') as asissue:
            content = asissue.read()

        sig = 'sha1=a39daaa400cc91fcc7a581214b607591d96d893d'
        headers = {
            'X-Github-Event': 'ping',
            'X-Hub-Signature': sig,  # signature of 'webhookevent_ping.json'
        }

        response = c.generic(
            'POST',
            f'{settings.URL_PREFIX}/octocat/webhook/{self.organization.pk}/',
            content,
            content_type='application/json',
            follow=True,
            **headers
        )
        self.assertTrue(response.status_code == HTTPStatus.OK, f'actual({response.status_code}) != expected({HTTPStatus.OK})')

    def _load_webhookevent(self, filepath: Path) -> Tuple[bytes, str]:
        with filepath.open('rb') as content_f:
            content = content_f.read()
            # calculate the 'X-Hub-Signature' header
            s = hmac.new(
                key=self.secret_encoded,
                msg=content,
                digestmod=hashlib.sha1,
            ).hexdigest()
            signature = f'sha1={s}'
        return content, signature

    def test_project_card_webhook_valid_signature(self):
        c = Client()
        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        content, signature = self._load_webhookevent(project_card_asissue_webhook_event_filepath)

        headers = {
            'HTTP_X_GITHUB_EVENT': 'project_card',
            'X-Hub-Signature': sig,
        }
        response = c.generic(
            'POST',
            f'{settings.URL_PREFIX}/octocat/webhook/{self.organization.pk}/',
            content,
            content_type='application/json',
            follow=True,
            **headers
        )
        self.assertTrue(response.status_code == HTTPStatus.CREATED, f'actual({response.status_code}) != expected({HTTPStatus.CREATED})')

        # confirm webhookevent is created
        self.assertTrue(GithubWebhookEvent.objects.count() == 1)
        webhook_events = GithubWebhookEvent.objects.all()
        for event in webhook_events:
            self.assertTrue(event.state == 'unprocessed', f'actual({event.state}) != expected(unprocessed)')

    def test_project_card_webhook_invalid_signature(self):
        c = Client()
        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        content, signature = self._load_webhookevent(project_card_asissue_webhook_event_filepath)
        invalid_signature = signature + 'x'

        headers = {
            'HTTP_X_GITHUB_EVENT': 'project_card',
            'X-Hub-Signature': invalid_signature,
        }
        response = c.generic(
            'POST',
            f'{settings.URL_PREFIX}/octocat/webhook/{self.organization.pk}/',
            content,
            content_type='application/json',
            follow=True,
            **headers
        )
        self.assertTrue(response.status_code == HTTPStatus.FORBIDDEN, f'actual({response.status_code}) != expected({HTTPStatus.FORBIDDEN})')

    def test_project_card_webhook_no_signature(self):
        c = Client()
        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        content, _ = self._load_webhookevent(project_card_asissue_webhook_event_filepath)
        headers = {
            'HTTP_X_GITHUB_EVENT': 'project_card',
        }
        response = c.generic(
            'POST',
            f'{settings.URL_PREFIX}/octocat/webhook/{self.organization.pk}/',
            content,
            content_type='application/json',
            follow=True,
            **headers
        )
        self.assertTrue(response.status_code == HTTPStatus.BAD_REQUEST)

    # created, edited, moved, converted, or deleted
    def test_queue_incoming_project_card_event__created(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asnote_webhookevent_created.payload'
        with event_filepath.open('r', encoding='utf8') as event_in:
            unquoted_payload = urllib.parse.unquote(event_in.read())
            payload = unquoted_payload.split('payload=')[-1]
            event = json.loads(payload)
        with self.assertRaises(KeyError) as context:
            queue_incoming_project_card_event(
                self.organization,
                event_type='project_card',
                event=event
            )

    def test_queue_incoming_project_card_event__edited(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_edited.json'
        with event_filepath.open('r', encoding='utf8') as event_in:
            event = json.load(event_in)
        prepared_webhookevent = queue_incoming_project_card_event(
            self.organization,
            event_type='project_card',
            event=event
        )
        self.assertTrue(prepared_webhookevent)

    def test_queue_incoming_project_card_event__moved(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_moved.payload'
        with event_filepath.open('r', encoding='utf8') as event_in:
            unquoted_payload = urllib.parse.unquote(event_in.read())
            payload = unquoted_payload.split('payload=')[-1]
            event = json.loads(payload)
        prepared_webhookevent = queue_incoming_project_card_event(
            self.organization,
            event_type='project_card',
            event=event
        )
        self.assertTrue(prepared_webhookevent)

    def test_queue_incoming_project_card_event__converted(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_converted.json'
        with event_filepath.open('r', encoding='utf8') as event_in:
            event = json.load(event_in)
        prepared_webhookevent = queue_incoming_project_card_event(
            self.organization,
            event_type='project_card',
            event=event
        )
        self.assertTrue(prepared_webhookevent)

    def test_queue_incoming_project_card_event__deleted(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_deleted.json'
        with event_filepath.open('r', encoding='utf8') as event_in:
            event = json.load(event_in)
        prepared_webhookevent = queue_incoming_project_card_event(
            self.organization,
            event_type='project_card',
            event=event
        )
        self.assertTrue(prepared_webhookevent)


