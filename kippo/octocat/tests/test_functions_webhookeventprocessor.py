import os
import json
import hashlib
import hmac
from typing import Tuple
from pathlib import Path
from http import HTTPStatus

from django.conf import settings
from django.utils import timezone
from django.test import TestCase, Client

from accounts.models import KippoUser, OrganizationMembership
from common.tests import setup_basic_project, DEFAULT_FIXTURES
from tasks.models import KippoTask, KippoTaskStatus

from ..models import GithubWebhookEvent
from ..functions import GithubWebhookProcessor

assert os.getenv('KIPPO_TESTING', False)  # The KIPPO_TESTING environment variable must be set to True

TESTDATA_DIRECTORY = Path(settings.BASE_DIR) / '..' / 'octocat' / 'testdata'


class OctocatFunctionsGithubWebhookProcessorTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.repository_name = 'myrepo'
        results = setup_basic_project(repository_name=self.repository_name)

        self.organization = results['KippoOrganization']
        self.secret_encoded = self.organization.webhook_secret.encode('utf8')
        self.project = results['KippoProject']
        self.user1 = results['KippoUser']
        self.github_manager = KippoUser.objects.get(username='github-manager')

        # create user2 for task assignement check
        self.user2 = KippoUser(
            username='octocat2',
            github_login='octocat2',
            password='test',
            email='octocat2@github.com',
            is_staff=True,
        )
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

    def test_issues_event__existing(self):
        # create existing task
        existing_task = KippoTask(
            title='kippo task title',
            project=self.project,
            assignee=self.user1,
            description='body',
            github_issue_api_url=f'https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository_name}/issues/9',
            github_issue_html_url=f'https://github.com/{self.organization.github_organization_name}/{self.repository_name}/issues/9',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_task.save()

        # create existing taskstatus
        existing_taskstatus = KippoTaskStatus(
            task=existing_task,
            state='open',
            effort_date=self.current_date,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_taskstatus.save()

        # create GithubWebhookEvent
        event_filepath = TESTDATA_DIRECTORY / 'issues_webhook_existing.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='issues',
            event=event
        )
        webhookevent.save()

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count == 1)

        # check updated webhookevents
        expected_unprocessed_events_count = 0
        actual_unprocessed_events_count = GithubWebhookEvent.objects.filter(state__in=('unprocessed', 'processing')).count()
        self.assertTrue(actual_unprocessed_events_count == expected_unprocessed_events_count)

        expected_processed_events_count = 1
        actual_processed_events_count = GithubWebhookEvent.objects.filter(state='processed').count()
        self.assertTrue(actual_processed_events_count == expected_processed_events_count, f'actual({actual_processed_events_count}) != expected({expected_processed_events_count}): {list(GithubWebhookEvent.objects.all())}')

        # check task updated
        existing_task.refresh_from_db()
        self.assertTrue(existing_task.title == event['issue']['title'], f'actual(title="{existing_task.title}") != expected(title="{event["issue"]["title"]}")')
        self.assertTrue(existing_task.assignee == self.user2, f'actual({existing_task.assignee.username}) != expected({self.user2.username})')

        # check taskstatus updated
        existing_taskstatus.refresh_from_db()
        self.assertTrue(existing_taskstatus.estimate_days == 1, f'actual({existing_taskstatus.estimate_days}) != expected({1})')  # as defined by "estimate:1d" label

    def test_issues_event__nonexisting_with_other_same_repo_task(self):
        existing_task = KippoTask(
            title='initial existing task title',
            project=self.project,
            assignee=self.user1,
            description='existing task body',
            github_issue_api_url=f'https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository_name}/issues/1',
            github_issue_html_url=f'https://github.com/{self.organization.github_organization_name}/{self.repository_name}/issues/1',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_task.save()

        # create GithubWebhookEvent
        event_filepath = TESTDATA_DIRECTORY / 'issues_webhook_existing.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='issues',
            event=event
        )
        webhookevent.save()
        event_issue_api_url = event['issue']['url']

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count == 1)

        # check updated webhookevents
        expected_unprocessed_events_count = 0
        actual_unprocessed_events_count = GithubWebhookEvent.objects.filter(state__in=('unprocessed', 'processing')).count()
        self.assertTrue(actual_unprocessed_events_count == expected_unprocessed_events_count)

        expected_processed_events_count = 1
        actual_processed_events_count = GithubWebhookEvent.objects.filter(state='processed').count()
        self.assertTrue(actual_processed_events_count == expected_processed_events_count, f'actual({actual_processed_events_count}) != expected({expected_processed_events_count}): {list(GithubWebhookEvent.objects.all())}')

        # check task was created
        tasks = KippoTask.objects.filter(github_issue_api_url=event_issue_api_url)
        self.assertTrue(tasks)
        task = tasks[0]

        self.assertTrue(task.title == event['issue']['title'], f'actual(title="{task.title}") != expected(title="{event["issue"]["title"]}")')
        self.assertTrue(task.assignee == self.user2, f'actual({task.assignee.username}) != expected({self.user2.username})')

        # check taskstatus updated
        taskstatuses = KippoTaskStatus.objects.filter(task=task)
        self.assertTrue(taskstatuses)
        taskstatus = taskstatuses[0]
        self.assertTrue(taskstatus.estimate_days == 1, f'actual({taskstatus.estimate_days}) != expected({1})')  # as defined by "estimate:1d" label

    def test_issues_event__nonexisting_with_no_same_repo_task(self):
        # create GithubWebhookEvent
        event_filepath = TESTDATA_DIRECTORY / 'issues_webhook_existing.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='issues',
            event=event
        )
        webhookevent.save()
        event_issue_api_url = event['issue']['url']

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count == 1)

        # check updated webhookevents
        expected_unprocessed_events_count = 0
        actual_unprocessed_events_count = GithubWebhookEvent.objects.filter(state__in=('unprocessed', 'processing')).count()
        self.assertTrue(actual_unprocessed_events_count == expected_unprocessed_events_count)

        expected_processed_events_count = 1
        actual_processed_events_count = GithubWebhookEvent.objects.filter(state='error').count()
        self.assertTrue(actual_processed_events_count == expected_processed_events_count, f'actual({actual_processed_events_count}) != expected({expected_processed_events_count}): {list(GithubWebhookEvent.objects.all())}')

        # check task was created
        tasks = KippoTask.objects.filter(github_issue_api_url=event_issue_api_url)
        self.assertFalse(tasks)

    def test_issuecomment_event(self):
        raise NotImplementedError

    def test_projectcard(self):
        raise NotImplementedError
