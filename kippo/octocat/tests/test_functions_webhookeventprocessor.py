import os
import json
import hashlib
import hmac
from typing import Tuple
from pathlib import Path

from django.utils import timezone
from django.test import TestCase

from accounts.models import KippoUser, OrganizationMembership
from common.tests import setup_basic_project, DEFAULT_FIXTURES
from tasks.models import KippoTask, KippoTaskStatus

from ..models import GithubWebhookEvent
from ..functions import GithubWebhookProcessor

assert os.getenv('KIPPO_TESTING', False)  # The KIPPO_TESTING environment variable must be set to True

TESTDATA_DIRECTORY = Path(__file__).parent / 'testdata'


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

    def test__get_events(self):
        # create GithubWebhookEvent
        event_type = 'project_card'
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()

        event_type = 'issues'
        event_filepath = TESTDATA_DIRECTORY / 'issues_webhook_existing.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()

        event_type = 'issue_comment'
        event_filepath = TESTDATA_DIRECTORY / 'issuecomment_webhook_created.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()

        # test event processor
        processor = GithubWebhookProcessor()
        events = list(processor._get_events())
        expected = 3
        actual = len(events)
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

        # confirm that the order of events is as expected
        expected = (
            'issues',
            'issue_comment',
            'project_card',
        )
        actual = tuple([e.event_type for e in events])
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

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
        event_type = 'issues'
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count[event_type] == 1)

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
        event_type = 'issues'
        event_filepath = TESTDATA_DIRECTORY / 'issues_webhook_existing.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()
        event_issue_api_url = event['issue']['url']

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count[event_type] == 1)

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
        event_type = 'issues'
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()
        event_issue_api_url = event['issue']['url']

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count[event_type] == 1)

        # check updated webhookevents
        expected_unprocessed_events_count = 0
        actual_unprocessed_events_count = GithubWebhookEvent.objects.filter(state__in=('unprocessed', 'processing')).count()
        self.assertTrue(actual_unprocessed_events_count == expected_unprocessed_events_count)

        expected_processed_events_count = 1
        actual_processed_events_count = GithubWebhookEvent.objects.filter(state='ignore').count()
        self.assertTrue(actual_processed_events_count == expected_processed_events_count, f'actual({actual_processed_events_count}) != expected({expected_processed_events_count}): {list(GithubWebhookEvent.objects.all())}')

        # check task is not created
        tasks = KippoTask.objects.filter(github_issue_api_url=event_issue_api_url)
        self.assertFalse(tasks)

    def test_issuecomment_event__existing_issue(self):
        # confirm that related KippoTaskStatus.last_comment is updated
        existing_task = KippoTask(
            title='initial existing task title',
            project=self.project,
            assignee=self.user1,
            description='existing task body',
            github_issue_api_url=f'https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository_name}/issues/9',
            github_issue_html_url=f'https://github.com/{self.organization.github_organization_name}/{self.repository_name}/issues/9',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_task.save()
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
        event_filepath = TESTDATA_DIRECTORY / 'issuecomment_webhook_created.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        event_type = 'issue_comment'
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()
        event_issue_api_url = event['issue']['url']

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()
        assert not existing_taskstatus.comment

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count[event_type] == 1)

        # check updated webhookevents
        expected_unprocessed_events_count = 0
        actual_unprocessed_events_count = GithubWebhookEvent.objects.filter(state__in=('unprocessed', 'processing')).count()
        self.assertTrue(actual_unprocessed_events_count == expected_unprocessed_events_count)

        expected_processed_events_count = 1
        actual_processed_events_count = GithubWebhookEvent.objects.filter(state='processed').count()
        self.assertTrue(actual_processed_events_count == expected_processed_events_count, f'actual({actual_processed_events_count}) != expected({expected_processed_events_count}): {list(GithubWebhookEvent.objects.all())}')

        # check that KippoTaskStatus.comment is updated
        existing_taskstatus.refresh_from_db()
        actual = existing_taskstatus.comment
        expected = 'octocat2 [ 2019-08-04T13:09:50Z ] comment+test'
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

    def test_issuecomment_event__nonexisting_issue(self):
        # create GithubWebhookEvent
        event_filepath = TESTDATA_DIRECTORY / 'issuecomment_webhook_created.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        event_type = 'issue_comment'
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()
        event_issue_api_url = event['issue']['url']

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count[event_type] == 1)

        # check updated webhookevents
        expected_unprocessed_events_count = 0
        actual_unprocessed_events_count = GithubWebhookEvent.objects.filter(state__in=('unprocessed', 'processing')).count()
        self.assertTrue(actual_unprocessed_events_count == expected_unprocessed_events_count)

        expected_processed_events_count = 1
        actual_processed_events_count = GithubWebhookEvent.objects.filter(state='ignore').count()
        self.assertTrue(actual_processed_events_count == expected_processed_events_count, f'actual({actual_processed_events_count}) != expected({expected_processed_events_count}): {list(GithubWebhookEvent.objects.all())}')

    def test_projectcard_event__existing_taskstatus(self):
        # confirm that related KippoTaskStatus.last_comment is updated
        existing_task = KippoTask(
            title='initial existing task title',
            project=self.project,
            assignee=self.user1,
            description='existing task body',
            github_issue_api_url=f'https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository_name}/issues/9',
            github_issue_html_url=f'https://github.com/{self.organization.github_organization_name}/{self.repository_name}/issues/9',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_task.save()
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
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        event_type = 'project_card'
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()
        # event_issue_api_url = event['project_card']['content_url']

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()
        assert not existing_taskstatus.comment

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count['project_card'] == 1, processed_event_count)

        # check that the existing task was updated with the project_card_id
        existing_task.refresh_from_db()
        expected = event['project_card']['id']
        actual = existing_task.project_card_id
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

        # check that KippoTaskStatus.state field was updated
        existing_taskstatus.refresh_from_db()
        expected = 'in-review'  # determined by project.column_info definition id:column_name mapping
        actual = existing_taskstatus.state
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

    def test_projectcard_event__nonexisting_taskstatus(self):
        # confirm that related KippoTaskStatus.last_comment is updated
        existing_task = KippoTask(
            title='initial existing task title',
            project=self.project,
            assignee=self.user1,
            description='existing task body',
            github_issue_api_url=f'https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository_name}/issues/9',
            github_issue_html_url=f'https://github.com/{self.organization.github_organization_name}/{self.repository_name}/issues/9',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_task.save()
        existing_taskstatus = KippoTaskStatus(
            task=existing_task,
            state='open',
            effort_date=timezone.datetime(2018, 1, 1).date(),
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_taskstatus.save()

        # create GithubWebhookEvent
        event_filepath = TESTDATA_DIRECTORY / 'project_card_asissue_webhookevent_created.json'
        event_encoded, _ = self._load_webhookevent(event_filepath)
        event = json.loads(event_encoded.decode('utf8'))
        event_type = 'project_card'
        webhookevent = GithubWebhookEvent(
            organization=self.organization,
            state='unprocessed',
            event_type=event_type,
            event=event
        )
        webhookevent.save()
        # event_issue_api_url = event['project_card']['content_url']

        unprocessed_events = 1
        assert unprocessed_events == GithubWebhookEvent.objects.count()
        assert not existing_taskstatus.comment

        # test event processor
        processor = GithubWebhookProcessor()
        processed_event_count = processor.process_webhook_events()
        self.assertTrue(processed_event_count['project_card'] == 1, processed_event_count)

        # check that the existing task was updated with the project_card_id
        existing_task.refresh_from_db()
        expected = event['project_card']['id']
        actual = existing_task.project_card_id
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')

        # check that KippoTaskStatus.state field was updated
        taskstatus = KippoTaskStatus.objects.get(task=existing_task, effort_date=self.current_date)
        expected = 'in-review'  # determined by project.column_info definition id:column_name mapping
        actual = taskstatus.state
        self.assertTrue(actual == expected, f'actual({actual}) != expected({expected})')
