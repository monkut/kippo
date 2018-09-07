import os
import json
from pathlib import Path
from django.conf import settings
from django.test import TestCase, Client

from common.tests import setup_basic_project, DEFAULT_FIXTURES
from .models import GithubWebhookEvent


assert os.getenv('KIPPO_TESTING', False)  # The KIPPO_TESTING environment variable must be set to True
TESTDATA_DIRECTORY = Path(settings.BASE_DIR) / '..' / 'octocat' / 'testdata'


class WebhookTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        setup_basic_project()

    def test_project_card_webhook(self):
        c = Client()

        project_card_asissue_webhook_event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhook_event.json'
        with project_card_asissue_webhook_event_filepath.open('r', encoding='utf8') as asissue:
            project_card_asissue_webhook_event_body = json.loads(asissue.read())

        response = c.post(f'{settings.URL_PREFIX}/octocat/webhook/',
                          content_type='application/json',
                          data=project_card_asissue_webhook_event_body,
                          follow=True,
                          X_GITHUB_EVENT='project_card')
        self.assertTrue(response.status_code == 201)

        # confirm webhook is created
        self.assertTrue(GithubWebhookEvent.objects.count() == 1)
        webhook_events = GithubWebhookEvent.objects.all()
        for event in webhook_events:
            self.assertTrue(event.state == 'processed', f'actual({event.state}) != expected(processed)')
