import json
import os
from pathlib import Path
from unittest import mock

from accounts.models import KippoUser, OrganizationMembership
from common.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from django.utils import timezone
from tasks.models import KippoTask, KippoTaskStatus

from ..functions import GithubWebhookProcessor
from ..models import GithubRepository, GithubWebhookEvent
from .utils import load_webhookevent

assert os.getenv("KIPPO_TESTING", False)  # The KIPPO_TESTING environment variable must be set to True

TESTDATA_DIRECTORY = Path(__file__).parent / "testdata"


class OctocatFunctionsGithubWebhookProcessorIssueLifecycleTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.repository_name = "myrepo"
        column_info = [
            {"id": "MDEzOlByb2plY3RDb2x1bW421", "name": "planning", "resourcePath": "/orgs/myorg/projects/21/columns/3769322"},
            {"id": "MDEzOlByb2plY3RDb2x1bW422", "name": "in-progress", "resourcePath": "/orgs/myorg/projects/21/columns/3769325"},
            {"id": "MDEzOlByb2plY3RDb2x1bW423", "name": "in-review", "resourcePath": "/orgs/myorg/projects/21/columns/4230564"},
            {"id": "MDEzOlByb2plY3RDb2x1bW424", "name": "done", "resourcePath": "/orgs/myorg/projects/21/columns/3769328"},
        ]

        results = setup_basic_project(repository_name=self.repository_name, github_project_api_id="1926922", column_info=column_info)

        self.organization = results["KippoOrganization"]
        self.secret_encoded = self.organization.webhook_secret.encode("utf8")
        self.project = results["KippoProject"]
        self.user1 = results["KippoUser"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # create user2 for task assignement check
        self.user2 = KippoUser(username="octocat2", github_login="octocat2", password="test", email="octocat2@github.com", is_staff=True)
        self.user2.save()

        orgmembership = OrganizationMembership(
            user=self.user2, organization=self.organization, is_developer=True, created_by=self.user2, updated_by=self.user2
        )
        orgmembership.save()
        self.current_date = timezone.now().date()

        # remove existing task/taskstatus
        KippoTaskStatus.objects.all().delete()
        KippoTask.objects.all().delete()

        self.githubwebhookprocessor = GithubWebhookProcessor()

    def test_webhookevent_issue_standard_lifecycle__same_assignment(self):
        assert KippoTask.objects.count() == 0
        scenario_directory = TESTDATA_DIRECTORY / "issue_standard_lifecycle_from_note"

        # issue created -- planning
        # -- on initial conversion from note the related 'GithubIssue' is known via the 'content_url'
        event_1_filepath = scenario_directory / "event_1_projectcard_converted_from_note.json"
        event_1, _ = load_webhookevent(event_1_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="project_card", event=event_1)
        webhookevent.save()

        issue_json = {"issue": json.loads((scenario_directory / "issue674_no_labels.json").read_text(encoding="utf8"))}
        issue_created = GithubWebhookProcessor._load_event_to_githubissue(issue_json)
        with mock.patch("ghorgs.managers.GithubOrganizationManager.get_github_issue", return_value=issue_created):
            self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertIsNone(latest_taskstatus.estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 0)

        event_2_filepath = scenario_directory / "event_2_issue_opened.json"
        event_2, _ = load_webhookevent(event_2_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_2)
        webhookevent.save()
        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertIsNone(latest_taskstatus.estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 0)

        # issue assigned
        # issue add estimate label - 1 day
        event_3_filepath = scenario_directory / "event_3_issue_labeled.json"
        event_3, _ = load_webhookevent(event_3_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_3)
        webhookevent.save()

        event_4_filepath = scenario_directory / "event_4_issue_labeled.json"
        event_4, _ = load_webhookevent(event_4_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_4)
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertEqual(latest_taskstatus.estimate_days, 1.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 1.0)

        # update estimate label - 5 days
        # - label added
        event_5_filepath = scenario_directory / "event_5_issue_labeled_changeestimate.json"
        event_5, _ = load_webhookevent(event_5_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_5)
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertEqual(latest_taskstatus.estimate_days, 5.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 5.0)

        # - label removed
        event_6_filepath = scenario_directory / "event_6_issue_labeled_changeesimate.json"
        event_6, _ = load_webhookevent(event_6_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_6)
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertEqual(latest_taskstatus.estimate_days, 5.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 5.0)

        # issue moved to in-progress
        # - no estimate update
        event_7_filepath = scenario_directory / "event_7_projectcard_moved.json"
        event_7, _ = load_webhookevent(event_7_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="project_card", event=event_7)
        webhookevent.save()

        issue_json = {"issue": json.loads((scenario_directory / "issue674_with_labels5days.json").read_text(encoding="utf8"))}
        issue_created = GithubWebhookProcessor._load_event_to_githubissue(issue_json)
        with mock.patch("ghorgs.managers.GithubOrganizationManager.get_github_issue", return_value=issue_created):
            self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "in-progress")
        self.assertEqual(latest_taskstatus.estimate_days, 5.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 5.0)

        # issue moved to done
        # - confirm issue estimate is no longer counted for the assignee
        event_8_filepath = scenario_directory / "event_8_projectcard_moved.json"
        event_8, _ = load_webhookevent(event_8_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="project_card", event=event_8)
        webhookevent.save()

        issue_json = {"issue": json.loads((scenario_directory / "issue674_with_labels5days.json").read_text(encoding="utf8"))}
        issue_created = GithubWebhookProcessor._load_event_to_githubissue(issue_json)
        with mock.patch("ghorgs.managers.GithubOrganizationManager.get_github_issue", return_value=issue_created):
            self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "done")
        self.assertEqual(latest_taskstatus.estimate_days, 5.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 0.0)

        # issue closed
        event_9_filepath = scenario_directory / "event_9_issue_closed.json"
        event_9, _ = load_webhookevent(event_9_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_9)
        webhookevent.save()

        issue_json = {"issue": json.loads((scenario_directory / "issue674_closed.json").read_text(encoding="utf8"))}
        issue_closed = GithubWebhookProcessor._load_event_to_githubissue(issue_json)
        with mock.patch("ghorgs.managers.GithubOrganizationManager.get_github_issue", return_value=issue_closed):
            self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), 1)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "done")
        self.assertEqual(latest_taskstatus.estimate_days, 5.0)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, 0.0)

        self.assertEqual(kippotask.is_closed, True)

    def test_webhook_issue__change_assignee(self):
        assert KippoTask.objects.count() == 0

        # create initially related task and status entry
        task1 = KippoTask(
            title="sample+title",
            category="cat1",
            github_issue_html_url="https://github.com/myorg/myrepo/issues/657",
            github_issue_api_url="https://api.github.com/repos/myorg/myrepo/issues/657",
            project=self.project,
            assignee=self.user1,  # octocat
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task1.save()
        taskstatus_estimate_days = 3
        task1status = KippoTaskStatus(
            task=task1,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=taskstatus_estimate_days,
            state="planning",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task1status.save()

        # confirm that initial user has related estimate days as expected
        assert self.user1.get_estimatedays() == taskstatus_estimate_days

        scenario_directory = TESTDATA_DIRECTORY / "issue_change_assignment"

        # issue created -- planning
        # -- on initial conversion from note the related 'GithubIssue' is known via the 'content_url'
        event_1_filepath = scenario_directory / "event_1_issue_unassigned.json"
        event_1, _ = load_webhookevent(event_1_filepath, secret_encoded=self.secret_encoded, decode=True)
        unassigned_webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_1)
        unassigned_webhookevent.save()

        event_2_filepath = scenario_directory / "event_2_issue_assigned.json"
        event_2, _ = load_webhookevent(event_2_filepath, secret_encoded=self.secret_encoded, decode=True)
        assigned_webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_2)
        assigned_webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([unassigned_webhookevent, assigned_webhookevent])
        unassigned_webhookevent.refresh_from_db()
        self.assertEqual(unassigned_webhookevent.state, "processed")
        assigned_webhookevent.refresh_from_db()
        self.assertEqual(assigned_webhookevent.state, "processed")

        # check that related task was changed/updated to new assignee
        task1.refresh_from_db()
        self.assertEqual(task1.assignee.github_login, self.user2.github_login)

        # check that new assignee (octocat2) has current KippoTaskStatus
        # -- NOTE: all TaskStatus is changed to new assignee
        actual_estimate_days = self.user2.get_estimatedays()
        self.assertEqual(actual_estimate_days, taskstatus_estimate_days)

        # make sure that originally assigned user does NOT have previously related estimate days
        self.assertEqual(self.user1.get_estimatedays(), 0.0)

    def test_webhookevents_issuefromnote__get_events(self):
        scenario_directory = TESTDATA_DIRECTORY / "issue_creation_from_note"
        for event_filepath in sorted(scenario_directory.glob("0*")):
            event, _ = load_webhookevent(event_filepath, secret_encoded=self.secret_encoded, decode=True)
            event_type = "project_card"
            if "issues" in event_filepath.name:
                event_type = "issues"
            webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type=event_type, event=event)
            webhookevent.save()
        assert GithubWebhookEvent.objects.all().count() == 6
        assert KippoTask.objects.all().count() == 0

        issue_json = {"issue": json.loads((scenario_directory / "issue43_opened.json").read_text(encoding="utf8"))}
        issue_opened = GithubWebhookProcessor._load_event_to_githubissue(issue_json)

        issue_json = {"issue": json.loads((scenario_directory / "issue43_assigned.json").read_text(encoding="utf8"))}
        issue_assigned = GithubWebhookProcessor._load_event_to_githubissue(issue_json)

        issue_json = {"issue": json.loads((scenario_directory / "issue43_labeled_1.json").read_text(encoding="utf8"))}
        issue_labeled_1 = GithubWebhookProcessor._load_event_to_githubissue(issue_json)

        issue_json = {"issue": json.loads((scenario_directory / "issue43_labeled_2.json").read_text(encoding="utf8"))}
        issue_labeled_2 = GithubWebhookProcessor._load_event_to_githubissue(issue_json)

        side_effects = (issue_opened, issue_assigned, issue_labeled_1, issue_labeled_2)
        with mock.patch("ghorgs.managers.GithubOrganizationManager.get_github_issue", side_effect=side_effects):
            self.githubwebhookprocessor.process_webhook_events()

        self.assertEqual(GithubWebhookEvent.objects.filter(state="processed").count(), 5)
        self.assertEqual(GithubWebhookEvent.objects.filter(state="ignore").count(), 1)

        tasks = list(KippoTask.objects.all())
        self.assertEqual(len(tasks), 1)

        task = tasks[0]
        self.assertEqual(task.assignee, self.user2)
        self.assertEqual(task.category, "setup")

        lastest_taskstatus = task.latest_kippotaskstatus()
        self.assertEqual(lastest_taskstatus.state, "planning")
        self.assertEqual(lastest_taskstatus.estimate_days, 3)

    def test_webhookevents_issuefromnote__get_events__new_repo(self):
        scenario_directory = TESTDATA_DIRECTORY / "issue_new_repository"
        for event_filepath in sorted(scenario_directory.glob("0*")):
            event, _ = load_webhookevent(event_filepath, secret_encoded=self.secret_encoded, decode=True)
            event_type = "project_card"
            if "issues" in event_filepath.name:
                event_type = "issues"
            webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type=event_type, event=event)
            webhookevent.save()
        assert GithubWebhookEvent.objects.all().count() == 6
        assert KippoTask.objects.all().count() == 0
        assert GithubRepository.objects.all().count() == 1

        issue_json = {"issue": json.loads((scenario_directory / "issue43_opened.json").read_text(encoding="utf8"))}
        issue_opened = GithubWebhookProcessor._load_event_to_githubissue(issue_json)

        issue_json = {"issue": json.loads((scenario_directory / "issue43_assigned.json").read_text(encoding="utf8"))}
        issue_assigned = GithubWebhookProcessor._load_event_to_githubissue(issue_json)

        issue_json = {"issue": json.loads((scenario_directory / "issue43_labeled_1.json").read_text(encoding="utf8"))}
        issue_labeled_1 = GithubWebhookProcessor._load_event_to_githubissue(issue_json)

        issue_json = {"issue": json.loads((scenario_directory / "issue43_labeled_2.json").read_text(encoding="utf8"))}
        issue_labeled_2 = GithubWebhookProcessor._load_event_to_githubissue(issue_json)

        side_effects = (issue_opened, issue_assigned, issue_labeled_1, issue_labeled_2)
        with mock.patch("ghorgs.managers.GithubOrganizationManager.get_github_issue", side_effect=side_effects):
            self.githubwebhookprocessor.process_webhook_events()

        self.assertEqual(GithubWebhookEvent.objects.filter(state="processed").count(), 5)
        self.assertEqual(GithubWebhookEvent.objects.filter(state="ignore").count(), 1)

        tasks = list(KippoTask.objects.all())
        self.assertEqual(len(tasks), 1)

        task = tasks[0]
        self.assertEqual(task.assignee, self.user2)
        self.assertEqual(task.category, "setup")

        lastest_taskstatus = task.latest_kippotaskstatus()
        self.assertEqual(lastest_taskstatus.state, "planning")
        self.assertEqual(lastest_taskstatus.estimate_days, 3)

        # check that previously undefined repo was added
        new_repos = list(GithubRepository.objects.filter(name="myotherrepo"))
        self.assertEqual(len(new_repos), 1)
        new_repo = new_repos[0]
        self.assertEqual(new_repo.name, "myotherrepo")
        self.assertEqual(new_repo.label_set, self.organization.default_labelset)

    # def test_webhookevent_issue_created_to_backlog_lifecycle(self):
    #     raise NotImplementedError
    #
    # def test_webhookevent_issue_created_to_cancel__lifecycle(self):
    #     raise NotImplementedError
