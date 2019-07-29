import os
import json
from pathlib import Path
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

    def test_project_card_webhook(self):
        c = Client()

        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        with project_card_asissue_webhook_event_filepath.open('r', encoding='utf8') as asissue:
            project_card_asissue_webhook_event_body = json.loads(asissue.read())

        response = c.post(f'{settings.URL_PREFIX}/octocat/webhook/{self.organization.pk}/',
                          content_type='application/json',
                          data=project_card_asissue_webhook_event_body,
                          follow=True,
                          X_GITHUB_EVENT='project_card')
        self.assertTrue(response.status_code == 201)

        # confirm webhookevent is created
        self.assertTrue(GithubWebhookEvent.objects.count() == 1)
        webhook_events = GithubWebhookEvent.objects.all()
        for event in webhook_events:
            self.assertTrue(event.state == 'unprocessed', f'actual({event.state}) != expected(unprocessed)')

        # confirm that event is processed

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
