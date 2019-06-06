import os
import json
from pathlib import Path
from unittest import mock
from django.conf import settings
from django.test import TestCase, Client

from common.tests import setup_basic_project, DEFAULT_FIXTURES
from .models import GithubWebhookEvent


assert os.getenv('KIPPO_TESTING', False)  # The KIPPO_TESTING environment variable must be set to True
TESTDATA_DIRECTORY = Path(settings.BASE_DIR) / '..' / 'octocat' / 'testdata'


def mocked_client_post_response_201(*_, **__):
    ok_res = {
        'msgId': 'rcsmessageid-19912',
    }

    class MockResponse:
        def __init__(self, json_data, status_code):
            self.json_data = json_data
            self.status_code = status_code
            self.content = json.dumps(ok_res)
            self.text = str(json_data)

        def raise_for_status(self):
            return True

        def json(self):
            return self.json_data

    return MockResponse(ok_res, 201)


class WebhookTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        setup_basic_project()

    #@mock.patch('Client.post', side_effect=mocked_client_post_response_201)
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
