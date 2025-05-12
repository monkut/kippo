"""Script for loading 'old' json dump files to the new (2019-7-26) db structure"""

import json
from argparse import ArgumentParser
from collections.abc import Generator
from gzip import decompress
from pathlib import Path

from accounts.models import Country, KippoOrganization, KippoUser, OrganizationMembership
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _
from octocat.models import GithubRepository, GithubRepositoryLabelSet
from projects.models import KippoProject, KippoProjectStatus, ProjectColumnSet
from tasks.models import KippoTask, KippoTaskStatus

try:
    CLI_USER = KippoUser.objects.get(username=settings.CLI_MANAGER_USERNAME)
except KippoUser.DoesNotExist as e:
    raise CommandError(f"Expected user not created: {settings.CLI_MANAGER_USERNAME}") from e


ADMIN_USER = KippoUser.objects.get(username="admin")
GITHUB_USER = KippoUser.objects.get(username="github-manager")
DEFAULT_LABELSET = GithubRepositoryLabelSet.objects.all()[0]
DEFAULT_COLUMNSET = ProjectColumnSet.objects.all()[0]
DEFAULT_ORG = KippoOrganization.objects.get(github_organization_name="kiconiaworks")


class DjangoJsonParser:
    def __init__(self, jsondump_filepath: Path) -> None:
        self.jsondump_filepath = jsondump_filepath
        self._data = None
        self.load()

    def load(self) -> None:
        with self.jsondump_filepath.open("rb") as jsongz_in:
            json_in = decompress(jsongz_in.read())
            self._data = json.loads(json_in.decode("utf8"))

    def _reformat_record(self, record: dict) -> dict:
        new_record = record["fields"]
        new_record["id"] = record["pk"]
        return new_record

    def get_modelrecords(self, modelname: str) -> Generator:
        for record in self._data:
            if record["model"] == modelname:
                yield self._reformat_record(record)


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: ArgumentParser):
        parser.add_argument("-j", "--jsongz", type=str, default=None, required=True, help=_("JSON Dump Gzip filepath"))
        parser.add_argument("-c", "--country", default="JPN", help="DEFAULT Country ALPHA3 code")

    def handle(self, *args, **options):  # noqa: C901,PLR0915,PLR0912
        jsongz_filepath = Path(options["jsongz"])
        if not jsongz_filepath.exists():
            raise CommandError(f"File not found: {options['jsongz']}")

        default_user_country = Country.objects.get(alpha_3=options["country"])
        jsonparser = DjangoJsonParser(jsongz_filepath)

        # load users
        existing_users = {u.github_login: u for u in KippoUser.objects.filter(github_login__isnull=False)}
        user_previous_id = {}
        json_users = []
        modelname = "accounts.kippouser"
        for result in jsonparser.get_modelrecords(modelname):
            existing_user = existing_users.get(result["github_login"], None)
            if existing_user:
                self.stdout.write(f"Using Existing: {existing_user} ({result['id']})")
                user_previous_id[result["id"]] = existing_user
                json_users.append(existing_user)
            elif result["github_login"]:
                user = KippoUser(
                    is_superuser=result["is_superuser"],
                    username=result["username"],
                    first_name=result["first_name"],
                    last_name=result["last_name"],
                    is_staff=result["is_active"],
                    github_login=result["github_login"],
                    holiday_country_id=default_user_country.id,
                )
                self.stdout.write(f"Create NEW: {user} ({result['id']})")
                user.save()
                user_previous_id[result["id"]] = user
                json_users.append(user)
            else:
                self.stderr.write(f"Skipping: {result['username']}")

        # get organization(s)
        existing_organizations = {o.name: o for o in KippoOrganization.objects.all()}
        organization_previous_id = {}
        added_organizations = []
        modelname = "accounts.kippoorganization"
        for result in jsonparser.get_modelrecords(modelname):
            existing_organization = existing_organizations.get(result["name"], None)
            if existing_organization:
                self.stdout.write(f"Using Existing: {existing_organization}")
                organization_previous_id[result["id"]] = existing_organization
                added_organizations.append(existing_organization)
            else:
                new_organization = KippoOrganization(
                    name=result["name"],
                    github_organization_name=result["github_organization_name"],
                    default_task_category=result["default_task_category"],
                    default_task_display_state=result["default_task_display_state"],
                    day_workhours=result["day_workhours"],
                    created_datetime=result["created_datetime"],
                    updated_datetime=result["updated_datetime"],
                    created_by=ADMIN_USER,
                    updated_by=ADMIN_USER,
                )
                self.stdout.write(f"Create NEW: {new_organization}")
                new_organization.save()
                organization_previous_id[result["id"]] = new_organization
                added_organizations.append(new_organization)

        # load organization memberships
        existing_memberships = {(o.user.github_login, o.organization.github_organization_name): o for o in OrganizationMembership.objects.all()}
        for user in json_users:
            for org in added_organizations:
                key = (user.github_login, org.github_organization_name)
                if key in existing_memberships:
                    membership = existing_memberships[key]
                    self.stdout.write(f"Use EXISTING: {membership}")
                else:
                    membership = OrganizationMembership(
                        user=user,
                        organization=org,
                        created_by=ADMIN_USER,
                        updated_by=ADMIN_USER,
                        is_project_manager=False,
                        is_developer=True,
                        sunday=False,
                        monday=True,
                        tuesday=True,
                        wednesday=True,
                        thursday=True,
                        friday=True,
                        saturday=True,
                    )
                    self.stdout.write(f"Create NEW: {membership}")
                    membership.save()

        # load projects
        previous_project_id = {}
        existing_projects = {p.name: p for p in KippoProject.objects.all()}
        modelname = "projects.kippoproject"
        for result in jsonparser.get_modelrecords(modelname):
            existing_project = existing_projects.get(result["name"])
            if existing_project:
                self.stdout.write(f"Using existing: {existing_project}")
                previous_project_id[result["id"]] = existing_project
            else:
                organization = organization_previous_id[result["organization"]]
                project = KippoProject(
                    name=result["name"],
                    is_closed=result["is_closed"],
                    confidence=result["confidence"],
                    document_url=result["document_url"],
                    phase=result["phase"],
                    start_date=result["start_date"],
                    target_date=result["target_date"],
                    actual_date=result["actual_date"],
                    problem_definition=result["problem_definition"],
                    display_as_active=result["display_as_active"],
                    created_datetime=result["created_datetime"],
                    updated_datetime=result["updated_datetime"],
                    closed_datetime=result["closed_datetime"],
                    organization=organization,
                    columnset=DEFAULT_COLUMNSET,
                    created_by=ADMIN_USER,
                    updated_by=ADMIN_USER,
                )
                self.stdout.write(f"Create NEW: {project}")
                project.save()
                previous_project_id[result["id"]] = project

        # load project status
        existing_projectstatuses = {p.comment: p for p in KippoProjectStatus.objects.all()}
        modelname = "projects.kippoprojectstatus"
        for result in jsonparser.get_modelrecords(modelname):
            existing_projectstatus = existing_projectstatuses.get(result["comment"], None)
            if not existing_projectstatus:
                created_by_user = user_previous_id.get(result["created_by"], ADMIN_USER)
                updated_by_user = user_previous_id.get(result["updated_by"], ADMIN_USER)
                projectstatus = KippoProjectStatus(
                    created_datetime=result["created_datetime"],
                    updated_datetime=result["updated_datetime"],
                    created_by=created_by_user,
                    updated_by=updated_by_user,
                    project=previous_project_id[result["project"]],
                    comment=result["comment"],
                )
                self.stdout.write(f"Creating NEW: {projectstatus}")
                projectstatus.save()

        # load github repositories
        existing_repos = {r.html_url: r for r in GithubRepository.objects.all()}
        modelname = "octocat.githubrepository"
        for result in jsonparser.get_modelrecords(modelname):
            existing_repo = existing_repos.get(result["html_url"])
            if not existing_repo:
                organization_id = result.get("organization", None)
                if organization_id:
                    organization = organization_previous_id[organization_id]
                else:
                    organization = DEFAULT_ORG
                repo = GithubRepository(
                    name=result["name"],
                    api_url=result["api_url"],
                    html_url=result["html_url"],
                    label_set=DEFAULT_LABELSET,
                    created_datetime=result["created_datetime"],
                    updated_datetime=result["updated_datetime"],
                    organization=organization,
                    created_by=GITHUB_USER,
                    updated_by=GITHUB_USER,
                )
                self.stdout.write(f"Create NEW: {repo}")
                repo.save()

        # load tasks
        task_previous_id = {}
        existing_tasks = {t.github_issue_html_url: t for t in KippoTask.objects.all()}
        modelname = "tasks.kippotask"
        for result in jsonparser.get_modelrecords(modelname):
            existing_task = existing_tasks.get(result["github_issue_html_url"])
            if existing_task:
                self.stdout.write(f"Use Existing: {existing_task}")
                task_previous_id[result["id"]] = existing_task
            else:
                result = dict(result)  # noqa: PLW2901
                previous_id = result["id"]

                result["project"] = previous_project_id[result["project"]]
                result.pop("project")

                result["assignee"] = user_previous_id[result["assignee"]]
                result.pop("assignee")

                result.pop("updated_by")
                result.pop("created_by")
                task = KippoTask(created_by=GITHUB_USER, updated_by=GITHUB_USER, **result)
                self.stdout.write(f"Create NEW: {task}")
                task.save()
                task_previous_id[previous_id] = task

        # load task status
        exisiting_taskstatuses = {(t.effort_date.strftime("%Y-%m-%d"), t.task_id): t for t in KippoTaskStatus.objects.all()}
        modelname = "tasks.kippotaskstatus"
        for result in jsonparser.get_modelrecords(modelname):
            key = (result["effort_date"], task_previous_id[result["task"]].id)
            exisiting_taskstatus = exisiting_taskstatuses.get(key)
            if not exisiting_taskstatus:
                result = dict(result)  # noqa: PLW2901
                result.pop("id")
                result.pop("created_by")
                result.pop("updated_by")

                result["task"] = task_previous_id[result["task"]]

                taskstatus = KippoTaskStatus(created_by=GITHUB_USER, updated_by=GITHUB_USER, **result)
                self.stdout.write(f"Create NEW: {taskstatus}")
                taskstatus.save()
