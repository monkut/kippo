import json
from http import HTTPStatus
from pathlib import Path

from accounts.models import KippoUser, OrganizationMembership
from commons.admin import KippoAdminSite
from commons.tests import DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase, setup_basic_project
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.db.models import Q
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from projects.models import KippoMilestone, KippoProject, ProjectColumnSet
from tasks.models import KippoTask, KippoTaskStatus

from ..admin import GithubMilestoneAdmin, GithubRepositoryAdmin, GithubRepositoryLabelSetAdmin, GithubWebhookEventAdmin
from ..models import GithubMilestone, GithubRepository, GithubRepositoryLabelSet, GithubWebhookEvent
from .utils import load_webhookevent

DEFAULT_COLUMNSET_PK = "414e69c8-8ea3-4c9c-8129-6f5aac108fa2"
TESTDATA_DIRECTORY = Path(__file__).parent / "testdata"


class IsStaffOrganizationAdminTestCase(IsStaffModelAdminTestCaseBase):
    def setUp(self):
        super().setUp()
        self.current_date = timezone.now().date()
        default_columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        # add GithubRepositories
        self.repository = GithubRepository.objects.create(
            organization=self.organization,
            name="myrepo",
            api_url="https://api.github.com/1",
            html_url="https://github.com/1",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_repository = GithubRepository.objects.create(
            organization=self.other_organization,
            name="myrepo2",
            api_url="https://api.github.com/2",
            html_url="https://github.com/2",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # add GithubMilestones
        # -- create project for milestones
        # -- create kippomilestones
        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name="project1",
            category="testing",
            columnset=default_columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone1 = KippoMilestone.objects.create(
            project=self.project1, title="milestone1", created_by=self.github_manager, updated_by=self.github_manager
        )

        self.project2 = KippoProject.objects.create(
            organization=self.other_organization,
            name="project2",
            category="testing",
            columnset=default_columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone2 = KippoMilestone.objects.create(
            project=self.project2, title="milestone2", created_by=self.github_manager, updated_by=self.github_manager
        )

        self.ghmilestone = GithubMilestone.objects.create(
            milestone=self.milestone1,
            repository=self.repository,
            number=123,
            api_url="https://api.github.com/milestone/1",
            html_url="https://github.com/milestone/1",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_ghmilestone = GithubMilestone.objects.create(
            milestone=self.milestone2,
            repository=self.other_repository,
            number=321,
            api_url="https://api.github.com/milestone/3",
            html_url="https://github.com/milestone/3",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # add GithubLabelsets
        self.githublabelset = GithubRepositoryLabelSet.objects.create(
            organization=self.organization,
            name="mytestlabelset",
            labels=[{"name": "category:X", "description": "", "color": "AED6F1"}],
        )
        self.other_githublabelset = GithubRepositoryLabelSet.objects.create(
            organization=self.other_organization,
            name="othertestlabelset",
            labels=[{"name": "category:X", "description": "", "color": "AED6F1"}],
        )

    def test_githubrepositoryadmin_list_objects(self):
        modeladmin = GithubRepositoryAdmin(GithubRepository, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # should list all
        all_count = GithubRepository.objects.count()
        self.assertTrue(all_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = GithubRepository.objects.filter(organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(
            len(queryset) == expected_count,
            f"actual({len(queryset)}) != expected({expected_count}): {', '.join(r.name for r in queryset)}",
        )

    def test_githubmilestoneadmin_list_objects(self):
        modeladmin = GithubMilestoneAdmin(GithubMilestone, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # should list all
        all_count = GithubMilestone.objects.count()
        self.assertTrue(all_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = GithubMilestone.objects.filter(repository__organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(
            len(queryset) == expected_count,
            f"actual({len(queryset)}) != expected({expected_count}): {', '.join(str(m.number) for m in queryset)}",
        )

    def test_githublabelsetadmin_list_objects(self):
        modeladmin = GithubRepositoryLabelSetAdmin(GithubRepositoryLabelSet, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # should list all
        all_count = GithubRepositoryLabelSet.objects.count()
        self.assertTrue(all_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = GithubRepositoryLabelSet.objects.filter(
            Q(organization__in=self.staff_user_request.user.organizations) | Q(organization__isnull=True)
        ).count()
        self.assertTrue(
            len(queryset) == expected_count,
            f"actual({len(queryset)}) != expected({expected_count}): {', '.join(r.name for r in queryset)}",
        )


class MockRequest:
    pass


class GithubWebhookEventAdminActionsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.site = KippoAdminSite()
        # create superuser and related request mock
        self.superuser_username = "superuser_no_org"
        self.superuser_no_org = KippoUser.objects.create(username=self.superuser_username, is_superuser=True, is_staff=True)
        self.super_user_request = MockRequest()
        self.super_user_request.user = self.superuser_no_org

        self.repository_name = "myrepo"
        results = setup_basic_project(repository_name=self.repository_name)

        self.organization = results["KippoOrganization"]
        self.secret_encoded = self.organization.webhook_secret.encode("utf8")
        self.project = results["KippoProject"]
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

        event_type = "issues"
        event_filepath = TESTDATA_DIRECTORY / "issues_webhook_existing.json"
        event_encoded, _ = load_webhookevent(event_filepath, secret_encoded=self.secret_encoded)
        event = json.loads(event_encoded.decode("utf8"))
        webhookevent = GithubWebhookEvent(organization=self.organization, state="unprocessed", event_type=event_type, event=event)
        webhookevent.save()

        self.client = Client()
        self.client.force_login(self.superuser_no_org)

        # create existing task
        existing_task = KippoTask(
            title="kippo task title",
            project=self.project,
            assignee=self.user1,
            description="body",
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository_name}/issues/9",
            github_issue_html_url=f"https://github.com/{self.organization.github_organization_name}/{self.repository_name}/issues/9",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_task.save()

        # create existing taskstatus
        existing_taskstatus = KippoTaskStatus(
            task=existing_task,
            state="open",
            effort_date=self.current_date,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        existing_taskstatus.save()

    def test_process_webhook_events_action(self):
        modeladmin = GithubWebhookEventAdmin(GithubWebhookEvent, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        data = {"action": "process_webhook_events", ACTION_CHECKBOX_NAME: [str(m.pk) for m in qs]}
        app_name = "octocat"
        model_name = "githubwebhookevent"
        change_url = reverse(f"admin:{app_name}_{model_name}_changelist")
        response = self.client.post(change_url, data, follow=True)
        assert response.status_code == HTTPStatus.OK, response.status_code

        actual = GithubWebhookEvent.objects.filter(state="processed").count()
        expected = GithubWebhookEvent.objects.all().count()
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

    def test_reset_webhook_events_action(self):
        # set state to error
        GithubWebhookEvent.objects.all().update(state="error")
        assert GithubWebhookEvent.objects.all().count() == GithubWebhookEvent.objects.filter(state="error").count()

        modeladmin = GithubWebhookEventAdmin(GithubWebhookEvent, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        data = {"action": "reset_webhook_events", ACTION_CHECKBOX_NAME: [str(m.pk) for m in qs]}
        app_name = "octocat"
        model_name = "githubwebhookevent"
        change_url = reverse(f"admin:{app_name}_{model_name}_changelist")
        response = self.client.post(change_url, data, follow=True)
        assert response.status_code == HTTPStatus.OK, response.status_code

        actual = GithubWebhookEvent.objects.filter(state="unprocessed").count()
        expected = GithubWebhookEvent.objects.all().count()
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")
