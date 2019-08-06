import os
import hashlib
import hmac
from typing import Tuple
from pathlib import Path
from http import HTTPStatus

from django.conf import settings
from django.test import TestCase, Client

from common.tests import setup_basic_project, DEFAULT_FIXTURES
from ..models import GithubWebhookEvent

assert os.getenv('KIPPO_TESTING', False)  # The KIPPO_TESTING environment variable must be set to True
TESTDATA_DIRECTORY = Path(settings.BASE_DIR) / '..' / 'octocat' / 'testdata'


class OctocatViewsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.client = Client()
        created = setup_basic_project()
        self.organization = created['KippoOrganization']
        self.secret_encoded = self.organization.webhook_secret.encode('utf8')
        GithubWebhookEvent.objects.all().delete()

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

    def test_application_xwwwformurlencoded(self):
        event_filepath = TESTDATA_DIRECTORY / 'issues_webhook_edited.payload'
        content, signature = self._load_webhookevent(event_filepath)

        headers = {
            'X-Github-Event': 'issues',
            'X-Hub-Signature': signature,
        }

        response = self.client.generic(
            'POST',
            self.organization.webhook_url,
            content,
            content_type='application/x-www-form-urlencoded',
            follow=True,
            **headers
        )
        expected = HTTPStatus.NO_CONTENT
        actual = response.status_code
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

        # confirm that GithubWebhookEvent is created
        self.assertTrue(GithubWebhookEvent.objects.count() == 1)

    def test_application_json(self):
        event_filepath = TESTDATA_DIRECTORY / 'issues_webhook_edited.json'
        content, signature = self._load_webhookevent(event_filepath)

        headers = {
            'X-Github-Event': 'issues',
            'X-Hub-Signature': signature,
        }
        response = self.client.generic(
            'POST',
            self.organization.webhook_url,
            content,
            content_type='application/json',
            follow=True,
            **headers
        )
        expected = HTTPStatus.NO_CONTENT
        actual = response.status_code
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected}): {response.content}')

        # confirm that GithubWebhookEvent is created
        self.assertTrue(GithubWebhookEvent.objects.count() == 1)

    def test_invalid_contenttype(self):
        event_filepath = TESTDATA_DIRECTORY / 'issues_webhook_edited.payload'
        content, signature = self._load_webhookevent(event_filepath)

        headers = {
            'X-Github-Event': 'issues',
            'X-Hub-Signature': signature,
        }
        response = self.client.generic(
            'POST',
            self.organization.webhook_url,
            content,
            content_type='text/html',
            follow=True,
            **headers
        )
        expected = HTTPStatus.BAD_REQUEST
        actual = response.status_code
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

        # confirm that GithubWebhookEvent is created
        self.assertTrue(GithubWebhookEvent.objects.count() == 0)
