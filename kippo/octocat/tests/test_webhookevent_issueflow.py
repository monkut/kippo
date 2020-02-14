import os
import json
import hashlib
import hmac
from typing import Tuple
from pathlib import Path
from unittest import mock

from django.utils import timezone
from django.test import TestCase

from accounts.models import KippoUser, OrganizationMembership
from common.tests import setup_basic_project, DEFAULT_FIXTURES
from tasks.models import KippoTask, KippoTaskStatus

from ..models import GithubWebhookEvent
from ..functions import GithubWebhookProcessor

assert os.getenv('KIPPO_TESTING', False)  # The KIPPO_TESTING environment variable must be set to True

TESTDATA_DIRECTORY = Path(__file__).parent / 'testdata'


class OctocatFunctionsGithubWebhookProcessorIssueLifecycleTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.repository_name = 'myrepo'
        column_info = [
            {'id': 'MDEzOlByb2plY3RDb2x1bW421', 'name': 'planning', 'resourcePath': '/orgs/myorg/projects/21/columns/3769322'},
            {'id': 'MDEzOlByb2plY3RDb2x1bW422', 'name': 'in-progress', 'resourcePath': '/orgs/myorg/projects/21/columns/3769325'},
            {'id': 'MDEzOlByb2plY3RDb2x1bW423', 'name': 'in-review', 'resourcePath': '/orgs/myorg/projects/21/columns/4230564'},
            {'id': 'MDEzOlByb2plY3RDb2x1bW424', 'name': 'done', 'resourcePath': '/orgs/myorg/projects/21/columns/3769328'},
        ]

        results = setup_basic_project(repository_name=self.repository_name, github_project_api_id='1926922', column_info=column_info)

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

        self.githubwebhookprocessor = GithubWebhookProcessor()

    def _load_webhookevent(self, filepath: Path, decode: bool = False) -> Tuple[bytes, str]:
        with filepath.open('rb') as content_f:
            content = content_f.read()
            # calculate the 'X-Hub-Signature' header
            s = hmac.new(
                key=self.secret_encoded,
                msg=content,
                digestmod=hashlib.sha1,
            ).hexdigest()
            signature = f'sha1={s}'
            if decode:
                content = json.loads(content)
        return content, signature

    def test_webhookevent_issue_standard_lifecycle__same_assignment(self):
        assert KippoTask.objects.count() == 0
        scenario_directory = TESTDATA_DIRECTORY / 'issue_standard_lifecycle_from_note'

        # issue created -- planning
        # -- on initial conversion from note the related 'GithubIssue' is known via the 'content_url'
        event_1_filepath = scenario_directory / 'event_1_projectcard_converted_from_note.json'
        event_1, _ = self._load_webhookevent(event_1_filepath, decode=True)
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='project_card',
            event=event_1
        )
        webhookevent.save()

        issue_json = {'issue': json.loads((scenario_directory / 'issue674_no_labels.json').read_text(encoding='utf8'))}
        issue_created = GithubWebhookProcessor._load_event_to_githubissue(issue_json)
        with mock.patch('ghorgs.managers.GithubOrganizationManager.get_github_issue', return_value=issue_created):
            self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, 'processed')
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, 'planning')
        self.assertIsNone(latest_taskstatus.estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 0)

        event_2_filepath = scenario_directory / 'event_2_issue_opened.json'
        event_2, _ = self._load_webhookevent(event_2_filepath, decode=True)
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='issues',
            event=event_2
        )
        webhookevent.save()
        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, 'processed')
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, 'planning')
        self.assertIsNone(latest_taskstatus.estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 0)

        # issue assigned
        # issue add estimate label - 1 day
        event_3_filepath = scenario_directory / 'event_3_issue_labeled.json'
        event_3, _ = self._load_webhookevent(event_3_filepath, decode=True)
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='issues',
            event=event_3
        )
        webhookevent.save()

        event_4_filepath = scenario_directory / 'event_4_issue_labeled.json'
        event_4, _ = self._load_webhookevent(event_4_filepath, decode=True)
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='issues',
            event=event_4
        )
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, 'processed')
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, 'planning')
        self.assertEqual(latest_taskstatus.estimate_days, 1.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 1.0)

        # update estimate label - 5 days
        # - label added
        event_5_filepath = scenario_directory / 'event_5_issue_labeled_changeestimate.json'
        event_5, _ = self._load_webhookevent(event_5_filepath, decode=True)
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='issues',
            event=event_5
        )
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, 'processed')
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, 'planning')
        self.assertEqual(latest_taskstatus.estimate_days, 5.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 5.0)

        # - label removed
        event_6_filepath = scenario_directory / 'event_6_issue_labeled_changeesimate.json'
        event_6, _ = self._load_webhookevent(event_6_filepath, decode=True)
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='issues',
            event=event_6
        )
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, 'processed')
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, 'planning')
        self.assertEqual(latest_taskstatus.estimate_days, 5.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 5.0)

        # issue moved to in-progress
        # - no estimate update
        event_7_filepath = scenario_directory / 'event_7_projectcard_moved.json'
        event_7, _ = self._load_webhookevent(event_7_filepath, decode=True)
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='project_card',
            event=event_7
        )
        webhookevent.save()

        issue_json = {'issue': json.loads((scenario_directory / 'issue674_with_labels5days.json').read_text(encoding='utf8'))}
        issue_created = GithubWebhookProcessor._load_event_to_githubissue(issue_json)
        with mock.patch('ghorgs.managers.GithubOrganizationManager.get_github_issue', return_value=issue_created):
            self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, 'processed')
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, 'in-progress')
        self.assertEqual(latest_taskstatus.estimate_days, 5.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 5.0)

        # issue moved to done
        # - confirm issue estimate is no longer counted for the assignee
        event_8_filepath = scenario_directory / 'event_8_projectcard_moved.json'
        event_8, _ = self._load_webhookevent(event_8_filepath, decode=True)
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type='project_card',
            event=event_8
        )
        webhookevent.save()

        issue_json = {'issue': json.loads((scenario_directory / 'issue674_with_labels5days.json').read_text(encoding='utf8'))}
        issue_created = GithubWebhookProcessor._load_event_to_githubissue(issue_json)
        with mock.patch('ghorgs.managers.GithubOrganizationManager.get_github_issue', return_value=issue_created):
            self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, 'processed')
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, 'done')
        self.assertEqual(latest_taskstatus.estimate_days, 5.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 0.0)


    #
    # def test_webhookevent_issue_created_to_backlog_lifecycle(self):
    #     raise NotImplementedError
    #
    # def test_webhookevent_issue_created_to_cancel__lifecycle(self):
    #     raise NotImplementedError