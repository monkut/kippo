import json
from pathlib import Path
from django.conf import settings
from django.test import TestCase, Client
from accounts.models import KippoOrganization, EmailDomain, KippoUser
from projects.models import KippoProject, ProjectColumnSet
from tasks.models import KippoTask
from .models import GithubWebhookEvent, GithubAccessToken


TESTDATA_DIRECTORY = Path(settings.BASE_DIR) / '..' / 'octocat' / 'testdata'


def setup_basic_project():
    user = KippoUser(
        username='octocat',
        password='test',
        email='a@github.com',
        is_staff=True,
        is_developer=True,
    )
    user.save(ignore_email_domain_check=True)

    organization = KippoOrganization(
        name='github',
        github_organization_name='githubcodesorg',
        created_by=user,
        updated_by=user,
    )
    organization.save()
    email_domain = EmailDomain(
        organization=organization,
        domain='github.com',
        is_staff_domain=True,
        created_by=user,
        updated_by=user,
    )
    email_domain.save()

    access_token = GithubAccessToken(
        organization=organization,
        token='kdakkfj',
        created_by=user,
        updated_by=user,
    )
    access_token.save()

    default_columnset = ProjectColumnSet.objects.get(pk=1)
    kippo_project = KippoProject(
        organization=organization,
        name='octocat-test-project',
        github_project_url='https://github.com/orgs/githubcodesorg/projects/1',
        columnset=default_columnset,
        created_by=user,
        updated_by=user,
    )
    kippo_project.save()

    kippo_task = KippoTask(
        title='githubcodesorg test task',
        category='test category',
        project=kippo_project,
        created_by=user,
        updated_by=user,
        github_issue_api_url="https://api.github.com/repos/octocat/Hello-World/issues/1347",
    )
    kippo_task.save()


class WebhookTestCase(TestCase):
    fixtures = [
        'required_bot_users',
        'default_columnset',
        'default_labelset',
    ]

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
