import os
import json
import hmac
from pathlib import Path
from http import HTTPStatus
from unittest import mock
from django.conf import settings
from django.test import TestCase, Client

from common.tests import setup_basic_project, DEFAULT_FIXTURES
from accounts.models import KippoOrganization
from .models import GithubWebhookEvent
from .functions import process_incoming_project_card_event


assert os.getenv('KIPPO_TESTING', False)  # The KIPPO_TESTING environment variable must be set to True
TESTDATA_DIRECTORY = Path(settings.BASE_DIR) / '..' / 'octocat' / 'testdata'


class WebhookTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        setup_basic_project()
        self.organization = KippoOrganization.objects.get(name='github')
        self.secret = 'abc1234'
        self.secret_encoded = self.secret.encode('utf8')
        self.organization.webhook_secret = self.secret
        self.organization.save()

    def test_project_card_webhook_valid_signature(self):
        c = Client()
        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        with project_card_asissue_webhook_event_filepath.open('rb') as asissue:
            content = asissue.read()
            # calculate the 'X-Hub-Signature' header
            s = hmac.new(self.secret_encoded + content).hexdigest()
            sig = f'sha1={s}'

            project_card_asissue_webhook_event_body = json.loads(content.decode('utf8'))
        headers = {
            'X_GITHUB_EVENT': 'project_card',
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
        with project_card_asissue_webhook_event_filepath.open('r', encoding='utf8') as asissue:
            content = asissue.read()
            # calculate the 'X-Hub-Signature' header
            content_encoded = content.encode('utf8')
            s = hmac.new(b'invalid text' + content_encoded).hexdigest()
            sig = f'sha1={s}'

            project_card_asissue_webhook_event_body = json.loads(content)
        headers = {
            'X_GITHUB_EVENT': 'project_card',
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
        self.assertTrue(response.status_code == HTTPStatus.FORBIDDEN)

    def test_project_card_webhook_no_signature(self):
        c = Client()
        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        with project_card_asissue_webhook_event_filepath.open('r', encoding='utf8') as asissue:
            content = asissue.read()
            project_card_asissue_webhook_event_body = json.loads(content)
        headers = {
            'X_GITHUB_EVENT': 'project_card',
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
    def test_process_incoming_project_card_event__created(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        with event_filepath.open('r', encoding='utf8') as event_in:
            event = json.load(event_in)
        prepared_webhookevent = process_incoming_project_card_event(self.organization, event)
        self.assertTrue(prepared_webhookevent)

    def test_process_incoming_project_card_event__edited(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_edited.json'
        with event_filepath.open('r', encoding='utf8') as event_in:
            event = json.load(event_in)
        prepared_webhookevent = process_incoming_project_card_event(self.organization, event)
        self.assertTrue(prepared_webhookevent)

    def test_process_incoming_project_card_event__moved(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_moved.json'
        with event_filepath.open('r', encoding='utf8') as event_in:
            event = json.load(event_in)
        prepared_webhookevent = process_incoming_project_card_event(self.organization, event)
        self.assertTrue(prepared_webhookevent)

    def test_process_incoming_project_card_event__converted(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_converted.json'
        with event_filepath.open('r', encoding='utf8') as event_in:
            event = json.load(event_in)
        prepared_webhookevent = process_incoming_project_card_event(self.organization, event)
        self.assertTrue(prepared_webhookevent)

    def test_process_incoming_project_card_event__deleted(self):
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_deleted.json'
        with event_filepath.open('r', encoding='utf8') as event_in:
            event = json.load(event_in)
        prepared_webhookevent = process_incoming_project_card_event(self.organization, event)
        self.assertTrue(prepared_webhookevent)
