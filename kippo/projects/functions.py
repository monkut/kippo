import datetime
import logging
from operator import attrgetter
from typing import TYPE_CHECKING, Generator, List, Optional, Tuple
from urllib.parse import urlsplit

from accounts.models import KippoOrganization, KippoUser
from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from ghorgs.managers import GithubOrganizationManager
from kippo.aws import upload_s3_csv
from zappa.asynchronous import task

if TYPE_CHECKING:
    from .models import KippoProject


logger = logging.getLogger(__name__)

TUESDAY_WEEKDAY = 2


def get_user_session_organization(request: HttpRequest) -> Tuple[KippoOrganization, List[KippoOrganization]]:
    """Retrieve the session defined user KippoOrganization"""
    # get organization defined in session
    organization_id = request.session.get("organization_id", None)
    logger.debug(f'session["organization_id"] for user({request.user.username}): {organization_id}')
    # check that user belongs to organization
    user_organizations = list(sorted(request.user.organizations, key=attrgetter("name")))
    user_organization_ids = {str(o.id): o for o in user_organizations}
    if not user_organization_ids:
        raise ValueError(f"No OrganizationMembership for user: {request.user.username}")

    if organization_id not in user_organization_ids.keys():
        # set to user first org
        # logger.debug(f"organization_id={organization_id} not in user_organization_ids.keys({user_organization_ids.keys()})")
        logger.warning(f'User({request.user.username}) invalid "organization_id" given, setting to "first".')
        organization = user_organizations[0]  # use first
        request.session["organization_id"] = str(organization_id)
    else:
        organization = user_organization_ids[organization_id]
    return organization, user_organizations


def collect_existing_github_projects(organization: KippoOrganization, as_user: KippoUser) -> List["KippoProject"]:
    """Collect existing github organizational projects for a configured KippoOrganization"""
    from .models import KippoProject

    manager = GithubOrganizationManager(organization=organization.github_organization_name, token=organization.githubaccesstoken.token)

    # get existing html_urls
    existing_html_urls = KippoProject.objects.filter(organization=organization, github_project_html_url__isnull=False).values_list(
        "github_project_html_url", flat=True
    )

    added_projects = []
    project_html_path_expected_path_component_count = 2
    for project in manager.projects():
        parsed_html_url = urlsplit(project.html_url)
        path_components = [c for c in parsed_html_url.path.split("/") if c]
        if len(path_components) == project_html_path_expected_path_component_count:
            if project.html_url not in existing_html_urls:
                # create related KippoProject
                kippo_project = KippoProject(
                    created_by=as_user,
                    updated_by=as_user,
                    organization=organization,
                    name=project.name,
                    columnset=organization.default_columnset,
                    github_project_html_url=project.html_url,
                )
                kippo_project.save()
                added_projects.append(kippo_project)
                logger.info(f"(collect_existing_github_projects) Created KippoProject: {project.name} {project.html_url}")
            else:
                logger.debug(f"(collect_existing_github_projects) Already Exists SKIPPING: {project.name}  {project.html_url}")
        else:
            logger.error(f"invalid path({parsed_html_url.path}), no KippoProject created: {project.name}  {project.html_url}")
    return added_projects


def get_kippoproject_taskstatus_csv_rows(
    kippo_project: "KippoProject", with_headers: bool = True, status_effort_date: Optional[datetime.date] = None
) -> Generator:
    """
    Generate the current 'active' taskstaus CSV lines for a given KippoProject
    """
    headers = (
        "kippo_task_id",
        "kippo_milestone",
        "github_issue_html_url",
        "category",
        "effort_date",
        "state",
        "estimate_days",
        "assignee_github_login",
        "latest_comment",
        "labels",
    )
    if with_headers:
        yield headers

    qs = kippo_project.get_latest_taskstatuses(active_only=True)
    qs = qs.order_by("state", "task__category", "task__github_issue_html_url")
    for taskstatus in qs:
        milestone = ""
        if taskstatus.task.milestone:
            milestone = taskstatus.task.milestone.title
        row = (
            taskstatus.task.id,
            milestone,
            taskstatus.task.github_issue_html_url,
            taskstatus.task.category,
            taskstatus.effort_date,
            taskstatus.state,
            taskstatus.estimate_days,
            taskstatus.task.assignee.github_login,
            taskstatus.comment,
            taskstatus.tags,
        )
        yield row


def previous_week_startdate(today: Optional[datetime.date] = None) -> datetime.datetime:
    """Get the previous week's start date"""
    MONDAY = 0
    week_start_day = MONDAY
    if not today:
        today = timezone.now().date()
    last_week = today - datetime.timedelta(days=5)
    current_date = last_week
    while current_date.weekday() != week_start_day:
        current_date -= datetime.timedelta(days=1)
    return current_date


@task
def generate_projectweeklyeffort_csv(user_id: str, key: str, from_datetime_isoformat: Optional[str] = None) -> None:
    from projects.models import KippoProject, ProjectWeeklyEffort

    user = KippoUser.objects.filter(pk=user_id).first()
    if not user:
        logger.error(f"KippoUser not found for given user_id({user_id}), projectweeklyeffort csv not generated!")
    else:
        projects = KippoProject.objects.filter(organization__in=user.organizations)
        effort_entries = ProjectWeeklyEffort.objects.filter(project__in=projects).order_by("project", "week_start", "user")
        if from_datetime_isoformat:
            from_datetime = datetime.datetime.fromisoformat(from_datetime_isoformat)
            logger.info(f"applying datetime filter: from_datetime={from_datetime}")
            effort_entries = effort_entries.filter(week_start__gte=from_datetime.date())

        headers = {"project": "project", "week_start": "week_start", "user": "user", "hours": "hours"}
        g = (
            {
                "project": effort.project.name,
                "week_start": effort.week_start.strftime("%Y%m%d"),
                "user": effort.user.display_name,
                "hours": effort.hours,
            }
            for effort in effort_entries
        )
        upload_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key, headers=headers, row_generator=g)


@task
def generate_projectstatuscomments_csv(project_ids: List[str], key: str) -> None:
    from projects.models import KippoProjectStatus

    projectstatus = KippoProjectStatus.objects.filter(project__id__in=project_ids).order_by("project__name", "created_datetime")

    headers = {"project": "project", "week_start": "week_start", "user": "user", "hours": "hours"}
    g = (
        {
            "project": status.project.name,
            "created_datetime": status.week_start.strftime("%Y%m%d"),
            "created_by": status.user.created_by.username,
            "comment": status.comment,
        }
        for status in projectstatus
    )
    upload_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key, headers=headers, row_generator=g)
