import calendar
import datetime
import logging
from collections import Counter
from collections.abc import Generator
from itertools import chain
from operator import attrgetter
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from accounts.functions import get_personal_holidays_generator
from accounts.models import KippoOrganization, KippoUser
from commons.definitions import MONDAY
from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from ghorgs.managers import GithubOrganizationManager
from zappa.asynchronous import task

from kippo.awsclients import upload_s3_csv

if TYPE_CHECKING:
    from .models import KippoProject


logger = logging.getLogger(__name__)

TUESDAY_WEEKDAY = 2


def get_user_session_organization(request: HttpRequest) -> tuple[KippoOrganization, list[KippoOrganization]]:
    """Retrieve the session defined user KippoOrganization"""
    # get organization defined in session
    organization_id = request.session.get("organization_id", None)
    logger.debug(f'session["organization_id"] for user({request.user.username}): {organization_id}')
    # check that user belongs to organization
    user_organizations = sorted(request.user.organizations, key=attrgetter("name"))
    user_organization_ids = {str(o.id): o for o in user_organizations}
    if not user_organization_ids:
        raise ValueError(f"No OrganizationMembership for user: {request.user.username}")

    if organization_id not in user_organization_ids:
        # set to user first org
        # logger.debug(f"organization_id={organization_id} not in user_organization_ids.keys({user_organization_ids.keys()})")
        logger.warning(f'User({request.user.username}) invalid "organization_id" given, setting to "first".')
        organization = user_organizations[0]  # use first
        request.session["organization_id"] = str(organization_id)
    else:
        organization = user_organization_ids[organization_id]
    return organization, user_organizations


def collect_existing_github_projects(organization: KippoOrganization, as_user: KippoUser) -> list["KippoProject"]:
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


def get_kippoproject_taskstatus_csv_rows(kippo_project: "KippoProject", with_headers: bool = True, _: datetime.date | None = None) -> Generator:
    """Generate the current 'active' taskstaus CSV lines for a given KippoProject"""
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


def previous_week_startdate(today: datetime.date | None = None) -> datetime.datetime:
    """Get the previous week's start date"""
    week_start_day = MONDAY
    if not today:
        today = timezone.now().date()
    last_week = today - datetime.timedelta(days=5)
    current_date = last_week
    while current_date.weekday() != week_start_day:
        current_date -= datetime.timedelta(days=1)
    return current_date


@task
def generate_projectweeklyeffort_csv(user_id: str, key: str, effort_ids: list[int], from_datetime_isoformat: str | None = None) -> None:
    from projects.models import KippoProject, ProjectWeeklyEffort

    user = KippoUser.objects.filter(pk=user_id).first()
    if not user:
        logger.error(f"KippoUser not found for given user_id({user_id}), projectweeklyeffort csv not generated!")
    else:
        projects = KippoProject.objects.filter(organization__in=user.organizations)
        effort_entries = ProjectWeeklyEffort.objects.filter(project__in=projects, id__in=effort_ids).order_by("project", "week_start", "user")
        from_datetime = None
        if from_datetime_isoformat:
            from_datetime = datetime.datetime.fromisoformat(from_datetime_isoformat)
            logger.info(f"applying datetime filter: from_datetime={from_datetime}")
            effort_entries = effort_entries.filter(week_start__gte=from_datetime.date())

        headers = {"project": "project", "week_start": "week_start", "user": "user", "hours": "hours"}
        weeklyeffort_generator = (
            {
                "project": effort.project.name,
                "week_start": effort.week_start.strftime("%Y%m%d"),
                "user": effort.user.display_name,
                "hours": effort.hours,
            }
            for effort in effort_entries
        )
        if settings.INCLUDE_PERSIONALHOLIDAYS_IN_WORKEFFORT_CSV:
            personal_holidays_generator = get_personal_holidays_generator(from_datetime)
            g = chain(weeklyeffort_generator, personal_holidays_generator)
        else:
            g = weeklyeffort_generator

        upload_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key, headers=headers, row_generator=g)


def generate_projectmonthlyeffort_csv(user_id: str, key: str, effort_ids: list[int], from_datetime_isoformat: str | None = None) -> None:
    from projects.models import KippoProject, ProjectWeeklyEffort

    user = KippoUser.objects.filter(pk=user_id).first()
    if not user:
        logger.error(f"KippoUser not found for given user_id({user_id}), projectmonthlyeffort csv not generated!")
        return

    projects = KippoProject.objects.filter(organization__in=user.organizations)
    effort_entries = ProjectWeeklyEffort.objects.filter(project__in=projects, id__in=effort_ids).order_by("project", "week_start", "user")

    if from_datetime_isoformat:
        from_datetime = datetime.datetime.fromisoformat(from_datetime_isoformat)
        logger.info(f"applying datetime filter: from_datetime={from_datetime}")
        effort_entries = effort_entries.filter(week_start__gte=from_datetime.date())

    monthly_effort = Counter()

    last_month_of_year = 12
    for effort in effort_entries:
        last_day_of_month = calendar.monthrange(effort.week_start.year, effort.week_start.month)[1]  # 各エントリの月の最後の日を取得
        current_month_remaining_days = last_day_of_month - effort.week_start.day + 1  # week_startからその月の最後まで何日か取得
        days_in_next_month = 7 - current_month_remaining_days  # 週が月をまたぐ時、次の月に週の何日あるか

        if days_in_next_month > 0:
            month_key_current = f"{effort.week_start.year}/{effort.week_start.month:02}"
            monthly_effort_key_current = (effort.project.name, effort.user.display_name, month_key_current)
            monthly_effort[monthly_effort_key_current] += (effort.hours * current_month_remaining_days) / 7

            if effort.week_start.month == last_month_of_year:
                next_month_year = effort.week_start.year + 1
                next_month_month = 1
            else:
                next_month_year = effort.week_start.year
                next_month_month = effort.week_start.month + 1

            month_key_next = f"{next_month_year}/{next_month_month:02}"
            monthly_effort_key_next = (effort.project.name, effort.user.display_name, month_key_next)
            monthly_effort[monthly_effort_key_next] += (effort.hours * days_in_next_month) / 7

        else:
            month_key = f"{effort.week_start.year}/{effort.week_start.month:02}"
            monthly_effort_key = (effort.project.name, effort.user.display_name, month_key)
            monthly_effort[monthly_effort_key] += effort.hours

    headers = {"project": "project", "user": "user", "month": "month", "totalhours": "totalhours"}
    monthlyeffort_generator = (
        {"project": project_name, "user": user_name, "month": month_key, "totalhours": hours}
        for (project_name, user_name, month_key), hours in monthly_effort.items()
    )

    upload_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key, headers=headers, row_generator=monthlyeffort_generator)


@task
def generate_projectstatuscomments_csv(project_ids: list[str], key: str) -> None:
    from projects.models import KippoProjectStatus

    projectstatus = KippoProjectStatus.objects.filter(project__id__in=project_ids).order_by("project__name", "created_datetime")

    headers = {
        "project": "project",
        "created_datetime": "created_datetime",
        "created_by": "created_by",
        "comment": "comment",
    }
    g = (
        {
            "project": status.project.name,
            "created_datetime": status.created_datetime.strftime("%Y%m%d"),
            "created_by": status.created_by.username,
            "comment": status.comment,
        }
        for status in projectstatus
    )
    upload_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key, headers=headers, row_generator=g)


@task
def generate_kippoprojectuserstatisfactionresult_csv(organization_pks: list[str], key: str) -> None:
    from projects.models import KippoProjectUserStatisfactionResult

    # get results for projects ending in the current fiscal year
    first_organization = KippoOrganization.objects.filter(pk__in=organization_pks).first()
    next_fiscal_year_datetime = first_organization.get_next_fiscal_year()
    logger.info(f"organization_pks={organization_pks}")
    logger.info(f"next_fiscal_year_datetime={next_fiscal_year_datetime}")
    results = KippoProjectUserStatisfactionResult.objects.filter(
        project__organization__pk__in=organization_pks,
        created_datetime__lte=next_fiscal_year_datetime,
    ).order_by("project", "created_by__username")
    headers = (
        "project_id",
        "project_name",
        "username",
        "fullfillment_score",
        "growth_score",
    )
    headers_dict = dict(zip(headers, headers, strict=False))
    g = (
        {
            "project_id": str(r.project.pk),
            "project_name": r.project.name,
            "username": r.created_by.username,
            "fullfillment_score": r.fullfillment_score,
            "growth_score": r.growth_score,
        }
        for r in results
    )
    upload_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key, headers=headers_dict, row_generator=g)


@task
def generate_kippoprojectusermonthlystatisfaction_csv(organization_pks: list[str], key: str) -> None:
    from projects.models import KippoProjectUserMonthlyStatisfactionResult

    first_organization = KippoOrganization.objects.filter(pk__in=organization_pks).first()
    next_fiscal_year_datetime = first_organization.get_next_fiscal_year()
    logger.info(f"organization_pks={organization_pks}")
    logger.info(f"next_fiscal_year_datetime={next_fiscal_year_datetime}")
    results = KippoProjectUserMonthlyStatisfactionResult.objects.filter(
        project__organization__pk__in=organization_pks,
        created_datetime__lte=next_fiscal_year_datetime,
    ).order_by("date", "project", "created_by__username")
    headers = (
        "project_id",
        "project_name",
        "date",
        "username",
        "fullfillment_score",
        "growth_score",
    )
    headers_dict = dict(zip(headers, headers, strict=False))
    g = (
        {
            "project_id": str(r.project.pk),
            "project_name": r.project.name,
            "date": r.date.isoformat(),
            "username": r.created_by.username,
            "fullfillment_score": r.fullfillment_score,
            "growth_score": r.growth_score,
        }
        for r in results
    )
    upload_s3_csv(bucket=settings.DUMPDATA_S3_BUCKETNAME, key=key, headers=headers_dict, row_generator=g)


def get_weekly_project_status_message(project: KippoProject, week_start: datetime.date | None = None) -> str:
    """
    Generate a weekly project status message for a given KippoProject and week_start date.
    Where
    """
    from .models import KippoProjectStatus

    if not week_start:
        week_start = previous_week_startdate()
    logger.debug(f"week_start={week_start}")
    assert not project.is_closed, f"Project is closed, cannot generate status message: {project}"
    KippoProjectStatus.objects.filter()

    raise NotImplementedError
