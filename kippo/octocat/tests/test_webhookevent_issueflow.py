import json
import os
from pathlib import Path
from unittest import mock

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from django.utils import timezone
from projects.models import KippoMilestone, KippoProject
from tasks.models import KippoTask, KippoTaskStatus

from ..functions import GithubWebhookProcessor
from ..models import GithubMilestone, GithubRepository, GithubWebhookEvent
from .utils import load_webhookevent

assert os.getenv("KIPPO_TESTING", None)  # The KIPPO_TESTING environment variable must be set to True

TESTDATA_DIRECTORY = Path(__file__).parent / "testdata"


class OctocatFunctionsGithubWebhookProcessorIssueLifecycleTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.repository_name = "myrepo"
        column_info = [
            {
                "id": "MDEzOlByb2plY3RDb2x1bW421",
                "name": "planning",
                "resourcePath": "/orgs/myorg/projects/21/columns/3769322",
            },
            {
                "id": "MDEzOlByb2plY3RDb2x1bW422",
                "name": "in-progress",
                "resourcePath": "/orgs/myorg/projects/21/columns/3769325",
            },
            {
                "id": "MDEzOlByb2plY3RDb2x1bW423",
                "name": "in-review",
                "resourcePath": "/orgs/myorg/projects/21/columns/4230564",
            },
            {
                "id": "MDEzOlByb2plY3RDb2x1bW424",
                "name": "done",
                "resourcePath": "/orgs/myorg/projects/21/columns/3769328",
            },
        ]

        results = setup_basic_project(repository_name=self.repository_name, github_project_api_id="1926922", column_info=column_info)

        self.organization = results["KippoOrganization"]
        self.secret_encoded = self.organization.github_webhook_secret.encode("utf8")
        self.project = results["KippoProject"]
        self.project2 = results["KippoProject2"]
        self.user1 = results["KippoUser"]

        self.github_manager = KippoUser.objects.get(username="github-manager")

        # create user2 for task assignement check
        self.user2 = KippoUser(username="octocat2", github_login="octocat2", password="test", email="octocat2@github.com", is_staff=True)  # noqa: S106
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

    def test_webhookevent_issue_standard_lifecycle__same_assignment(self):  # noqa: PLR0915
        initial_task_count = 0
        assert KippoTask.objects.count() == initial_task_count
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
        expected_task_count = 1
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), expected_task_count)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertIsNone(latest_taskstatus.estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        expected_assigned_user_estimate_days = 0
        self.assertEqual(user_estimatedays, expected_assigned_user_estimate_days)

        event_2_filepath = scenario_directory / "event_2_issue_opened.json"
        event_2, _ = load_webhookevent(event_2_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_2)
        webhookevent.save()
        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        expected_task_count = 1
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), expected_task_count)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertIsNone(latest_taskstatus.estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, expected_assigned_user_estimate_days)

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
        self.assertEqual(KippoTask.objects.count(), expected_task_count)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        expected_estimate_days = 1.0
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertEqual(latest_taskstatus.estimate_days, expected_estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, expected_estimate_days)

        # update estimate label - 5 days
        # - label added
        event_5_filepath = scenario_directory / "event_5_issue_labeled_changeestimate.json"
        event_5, _ = load_webhookevent(event_5_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_5)
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")
        self.assertEqual(KippoTask.objects.count(), expected_task_count)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        expected_estimate_days = 5.0
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertEqual(latest_taskstatus.estimate_days, expected_estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, expected_estimate_days)

        # - label removed
        event_6_filepath = scenario_directory / "event_6_issue_labeled_changeesimate.json"
        event_6, _ = load_webhookevent(event_6_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_6)
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])

        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")

        self.assertEqual(KippoTask.objects.count(), expected_task_count)
        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()

        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "planning")
        self.assertEqual(latest_taskstatus.estimate_days, expected_estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, expected_estimate_days)

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
        expected_estimate_days = 5.0
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "in-progress")
        self.assertEqual(latest_taskstatus.estimate_days, expected_estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, expected_estimate_days)

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
        expected_task_count = 1
        self.assertEqual(KippoTask.objects.count(), expected_task_count)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "done")
        self.assertEqual(latest_taskstatus.estimate_days, expected_estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        expected_user_estimate_days = 0.0
        self.assertEqual(user_estimatedays, expected_user_estimate_days)

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
        self.assertEqual(KippoTask.objects.count(), expected_task_count)

        kippotask = KippoTask.objects.latest()

        # check KippoTaskStatus
        latest_taskstatus = kippotask.latest_kippotaskstatus()
        self.assertTrue(latest_taskstatus)
        self.assertEqual(latest_taskstatus.state, "done")
        self.assertEqual(latest_taskstatus.estimate_days, expected_estimate_days)

        # check assigned user total estimate days
        user_estimatedays = self.user1.get_estimatedays()
        self.assertEqual(user_estimatedays, expected_user_estimate_days)

        self.assertEqual(kippotask.is_closed, True)

    def test_webhook_issue__change_assignee(self):
        initial_task_count = 0
        assert KippoTask.objects.count() == initial_task_count

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
        expected_user_estimate_days = 0.0
        self.assertEqual(self.user1.get_estimatedays(), expected_user_estimate_days)

    def test_webhookevents_issuefromnote__get_events(self):
        scenario_directory = TESTDATA_DIRECTORY / "issue_creation_from_note"
        for event_filepath in sorted(scenario_directory.glob("0*")):
            event, _ = load_webhookevent(event_filepath, secret_encoded=self.secret_encoded, decode=True)
            event_type = "project_card"
            if "issues" in event_filepath.name:
                event_type = "issues"
            webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type=event_type, event=event)
            webhookevent.save()
        expected_webhookevent_count = 6
        assert GithubWebhookEvent.objects.all().count() == expected_webhookevent_count
        initial_task_count = 0
        assert KippoTask.objects.all().count() == initial_task_count

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

        expected_processed_event_count = 5
        expected_ignoreed_event_count = 1
        self.assertEqual(GithubWebhookEvent.objects.filter(state="processed").count(), expected_processed_event_count)
        self.assertEqual(GithubWebhookEvent.objects.filter(state="ignore").count(), expected_ignoreed_event_count)

        tasks = list(KippoTask.objects.all())
        expected_task_count = 1
        self.assertEqual(len(tasks), expected_task_count)

        task = tasks[0]
        self.assertEqual(task.assignee, self.user2)
        self.assertEqual(task.category, "setup")

        lastest_taskstatus = task.latest_kippotaskstatus()
        self.assertEqual(lastest_taskstatus.state, "planning")
        expected_estimate_days = 3
        self.assertEqual(lastest_taskstatus.estimate_days, expected_estimate_days)

    def test_webhookevents_issuefromnote__get_events__new_repo(self):
        scenario_directory = TESTDATA_DIRECTORY / "issue_new_repository"
        for event_filepath in sorted(scenario_directory.glob("0*")):
            event, _ = load_webhookevent(event_filepath, secret_encoded=self.secret_encoded, decode=True)
            event_type = "project_card"
            if "issues" in event_filepath.name:
                event_type = "issues"
            webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type=event_type, event=event)
            webhookevent.save()

        expected_event_count = 6
        assert GithubWebhookEvent.objects.all().count() == expected_event_count
        expected_task_count = 0
        assert KippoTask.objects.all().count() == expected_task_count
        expected_repo_count = 1
        assert GithubRepository.objects.all().count() == expected_repo_count

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

        expected_processed_event_count = 5
        expected_ignoreed_event_count = 1
        self.assertEqual(GithubWebhookEvent.objects.filter(state="processed").count(), expected_processed_event_count)
        self.assertEqual(GithubWebhookEvent.objects.filter(state="ignore").count(), expected_ignoreed_event_count)

        tasks = list(KippoTask.objects.all())
        expected_task_count = 1
        self.assertEqual(len(tasks), expected_task_count)

        task = tasks[0]
        self.assertEqual(task.assignee, self.user2)
        self.assertEqual(task.category, "setup")

        lastest_taskstatus = task.latest_kippotaskstatus()
        self.assertEqual(lastest_taskstatus.state, "planning")
        expected_planning_estimate_days = 3
        self.assertEqual(lastest_taskstatus.estimate_days, expected_planning_estimate_days)

        # check that previously undefined repo was added
        new_repos = list(GithubRepository.objects.filter(name="myotherrepo"))
        expected_repo_count = 1
        self.assertEqual(len(new_repos), expected_repo_count)
        new_repo = new_repos[0]
        self.assertEqual(new_repo.name, "myotherrepo")
        self.assertEqual(new_repo.label_set, self.organization.default_labelset)

    def test_webhookevent_issue_unassigned_closed_task_github_user_removed(self):
        initial_task_count = 0
        assert KippoTask.objects.count() == initial_task_count

        # --> to test multiple tasks created issue
        initial_project_count = 2
        assert KippoProject.objects.count() == initial_project_count

        # create initially related task and status entry
        task0 = KippoTask(
            title="sample+title",
            category="cat1",
            github_issue_html_url="https://github.com/myorg/myrepo/issues/2",
            github_issue_api_url="https://api.github.com/repos/myorg/myrepo/issues/2",
            project=self.project,
            assignee=self.user1,  # octocat
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task0.save()
        taskstatus_estimate_days = 3
        task0status = KippoTaskStatus(
            task=task0,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=taskstatus_estimate_days,
            state="done",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task0status.save()

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
            state="done",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task1status.save()

        # create task in same repository in other project
        task2 = KippoTask(
            title="sample+title2",
            category="cat1",
            github_issue_html_url="https://github.com/myorg/myrepo/issues/1",
            github_issue_api_url="https://api.github.com/repos/myorg/myrepo/issues/1",
            project=self.project2,
            assignee=self.user1,  # octocat
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task2.save()
        taskstatus_estimate_days = 3
        task2status = KippoTaskStatus(
            task=task2,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=taskstatus_estimate_days,
            state="done",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task2status.save()

        # confirm that initial user has related estimate days as expected
        assert self.user1.get_estimatedays() == 0  # task is "done" and therefore does not issue a estimate

        scenario_directory = TESTDATA_DIRECTORY / "github_user_removed"

        # issue created -- planning
        # -- on initial conversion from note the related 'GithubIssue' is known via the 'content_url'
        event_1_filepath = scenario_directory / "issues_webhook_unassigned.json"
        event_1, _ = load_webhookevent(event_1_filepath, secret_encoded=self.secret_encoded, decode=True)
        unassigned_webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=event_1)
        unassigned_webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([unassigned_webhookevent])
        unassigned_webhookevent.refresh_from_db()
        self.assertEqual(unassigned_webhookevent.state, "processed")

        task_count = KippoTask.objects.filter(github_issue_html_url="https://github.com/myorg/myrepo/issues/657").count()
        self.assertEqual(task_count, 1)

        # check that related task was changed/updated to new assignee
        task1 = KippoTask.objects.get(github_issue_html_url="https://github.com/myorg/myrepo/issues/657")
        organization_unassigned_kippouser = self.organization.get_unassigned_kippouser()
        self.assertEqual(task1.assignee, organization_unassigned_kippouser)

        # check that new assignee (octocat2) has current KippoTaskStatus
        # -- NOTE: all TaskStatus is changed to new assignee
        actual_estimate_days = organization_unassigned_kippouser.get_estimatedays()
        self.assertEqual(actual_estimate_days, 0.0)

        # check that a new status is created
        latest_kippotaskstatus = task1.latest_kippotaskstatus()
        self.assertNotEqual(latest_kippotaskstatus.id, task1status.id)

        self.assertEqual(latest_kippotaskstatus.state, "done")

    def test_webhookevent_issue_existing_issues_milestoned__with_existing_milestone(self):
        initial_task_count = 0
        assert KippoTask.objects.count() == initial_task_count

        # --> to test multiple tasks created issue
        initial_project_count = 2
        assert KippoProject.objects.count() == initial_project_count

        initial_kippo_milestone_count = 0
        assert KippoMilestone.objects.count() == initial_kippo_milestone_count

        milestone_startdate = timezone.datetime(2019, 6, 1).date()
        milestone_enddate = timezone.datetime(2019, 6, 10).date()

        # create existing KippoMilestone/GithubMilestone
        kippo_milestone = KippoMilestone(project=self.project, title="milestone1", start_date=milestone_startdate, target_date=milestone_enddate)
        kippo_milestone.save()

        github_milestone_number = 5
        github_milestone_api_url = f"https://api.github.com/repos/myorg/myrepo/milestones/{github_milestone_number}"
        github_milestone_html_url = f"https://github.com/myorg/myrepo/milestone/{github_milestone_number}"
        github_milestone = GithubMilestone(
            milestone=kippo_milestone,
            number=github_milestone_number,
            api_url=github_milestone_api_url,
            html_url=github_milestone_html_url,
        )
        github_milestone.save()

        # create initially related task and status entry
        issue_number = 809
        task0 = KippoTask(
            title="sample+title",
            category="cat1",
            github_issue_html_url=f"https://github.com/myorg/myrepo/issues/{issue_number}",
            github_issue_api_url=f"https://api.github.com/repos/myorg/myrepo/issues/{issue_number}",
            project=self.project,
            assignee=self.user2,  # octocat2
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task0.save()
        taskstatus_estimate_days = 3
        task0status = KippoTaskStatus(
            task=task0,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=taskstatus_estimate_days,
            state="done",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task0status.save()

        # issue milestoned
        # -- on initial conversion from note the related 'GithubIssue' is known via the 'content_url'
        milestoned_event_filepath = TESTDATA_DIRECTORY / "issues_webhook_milestoned.json"
        milestoned_event, _ = load_webhookevent(milestoned_event_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=milestoned_event)
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])
        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")

        task0.refresh_from_db()
        self.assertEqual(task0.milestone, kippo_milestone)

    def test_webhookevent_issue_existing_issues_milestoned__without_existing_milestone(self):
        initial_task_count = 0
        assert KippoTask.objects.count() == initial_task_count

        # --> to test multiple tasks created issue
        initial_project_count = 2
        assert KippoProject.objects.count() == initial_project_count

        initial_kippo_milestone_count = 0
        assert KippoMilestone.objects.count() == initial_kippo_milestone_count
        initial_github_milestone_count = 0
        assert GithubMilestone.objects.count() == initial_github_milestone_count

        # create initially related task and status entry
        issue_number = 809
        task0 = KippoTask(
            title="sample+title",
            category="cat1",
            github_issue_html_url=f"https://github.com/myorg/myrepo/issues/{issue_number}",
            github_issue_api_url=f"https://api.github.com/repos/myorg/myrepo/issues/{issue_number}",
            project=self.project,
            assignee=self.user2,  # octocat2
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task0.save()
        taskstatus_estimate_days = 3
        task0status = KippoTaskStatus(
            task=task0,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=taskstatus_estimate_days,
            state="done",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task0status.save()

        # issue milestoned
        # -- on initial conversion from note the related 'GithubIssue' is known via the 'content_url'
        milestoned_event_filepath = TESTDATA_DIRECTORY / "issues_webhook_milestoned.json"
        milestoned_event, _ = load_webhookevent(milestoned_event_filepath, secret_encoded=self.secret_encoded, decode=True)
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type="issues", event=milestoned_event)
        webhookevent.save()

        self.githubwebhookprocessor.process_webhook_events([webhookevent])
        webhookevent.refresh_from_db()
        self.assertEqual(webhookevent.state, "processed")

        expected = 1
        self.assertEqual(KippoMilestone.objects.count(), expected)

        expected = 1
        self.assertEqual(GithubMilestone.objects.count(), expected)

        github_milestone = GithubMilestone.objects.all()[0]

        expected_github_milestone_number = 5  # number from event
        self.assertEqual(github_milestone.number, expected_github_milestone_number)

        expected_github_milestone_api_url = f"https://api.github.com/repos/myorg/myrepo/milestones/{expected_github_milestone_number}"
        self.assertEqual(github_milestone.api_url, expected_github_milestone_api_url)

        github_milestone_html_url = f"https://github.com/myorg/myrepo/milestone/{expected_github_milestone_number}"
        self.assertEqual(github_milestone.html_url, github_milestone_html_url)

        expected_milestone_enddate = timezone.datetime(2020, 10, 11).date()  # due_on
        kippo_milestone = KippoMilestone.objects.all()[0]
        self.assertEqual(kippo_milestone.start_date, None)  # webhook event created milestone does not have start_date set
        self.assertEqual(kippo_milestone.target_date, expected_milestone_enddate)
        # internal management number (unrelated to GithubMilestone.number
        expected = 0
        self.assertEqual(kippo_milestone.number, expected)
