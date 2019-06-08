from accounts.models import KippoOrganization, EmailDomain, KippoUser, OrganizationMembership
from projects.models import KippoProject, ProjectColumnSet
from tasks.models import KippoTask
from octocat.models import GithubAccessToken


DEFAULT_FIXTURES = [
    'required_bot_users',
    'default_columnset',
    'default_labelset',
]


def setup_basic_project():
    created_objects = {}
    user = KippoUser(
        username='octocat',
        password='test',
        email='a@github.com',
        is_staff=True,
    )
    user.save()
    created_objects['KippoUser'] = user

    organization = KippoOrganization(
        name='github',
        github_organization_name='githubcodesorg',
        day_workhours=8,
        created_by=user,
        updated_by=user,
    )
    organization.save()
    created_objects['KippoOrganization'] = organization

    email_domain = EmailDomain(
        organization=organization,
        domain='github.com',
        is_staff_domain=True,
        created_by=user,
        updated_by=user,
    )
    email_domain.save()
    created_objects['EmailDomain'] = email_domain

    orgmembership = OrganizationMembership(
        user=user,
        organization=organization,
        is_developer=True,
        created_by=user,
        updated_by=user,
    )
    orgmembership.save()

    access_token = GithubAccessToken(
        organization=organization,
        token='kdakkfj',
        created_by=user,
        updated_by=user,
    )
    access_token.save()
    created_objects['GithubAccessToken'] = access_token

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
    created_objects['KippoProject'] = kippo_project

    kippo_task = KippoTask(
        title='githubcodesorg test task',
        category='test category',
        project=kippo_project,
        created_by=user,
        updated_by=user,
        github_issue_api_url="https://api.github.com/repos/octocat/Hello-World/issues/1347",
    )
    kippo_task.save()
    created_objects['KippoTask'] = kippo_task

    return created_objects
