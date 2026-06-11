import datetime
import logging
import uuid
from collections import Counter
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

import reversion
from accounts.models import KippoOrganization, KippoUser, OrganizationMembership, PublicHoliday
from commons.fields import CommaSeparatedCharField
from commons.functions import first_of_month, first_of_next_month, last_of_month
from commons.models import TimestampedModel, UserCreatedBaseModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Max, QuerySet, Sum
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from ghorgs.managers import GithubOrganizationManager
from tasks.models import KippoTaskStatus

from .definitions import (
    BILLING_TYPE_MONTHLY,
    DEFAULT_BILLING_TYPE,
    DEFAULT_PROJECT_CATEGORY_VALUE,
    KIPPOPROJECT_CATEGORY_MAX_LENGTH,
    VALID_BILLING_TYPES,
    ProjectProgressStatus,
    ProjectRoles,
    ValidCurrencies,
    ValidServices,
)
from .exceptions import ProjectColumnSetError
from .functions import previous_week_startdate

if TYPE_CHECKING:
    from decimal import Decimal

logger = logging.getLogger(__name__)

UNASSIGNED_USER_GITHUB_LOGIN_PREFIX = settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX
GITHUB_MANAGER_USERNAME = settings.GITHUB_MANAGER_USERNAME
UNPROCESSABLE_ENTITY_422 = 422
MAX_ASSIGNMENT_PERCENTAGE = 100


def get_target_date_default() -> datetime.date:
    # TODO: update to take into account configured holidays
    return (timezone.now() + timezone.timedelta(days=settings.DEFAULT_KIPPORPOJECT_TARGET_DATE_DAYS)).date()


def category_prefixes_default():
    return ["category:", "cat:"]


def estimate_prefixes_default():
    return ["estimate:", "est:"]


def _normalize_slack_channel_name(value: str) -> str:
    return value.strip().lstrip("#") if value else value


class ProjectColumnSet(models.Model):  # not using userdefined model in order to make model definitions more portable
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.KippoOrganization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        editable=False,
        help_text=_("The organization that the columnset belongs to(if null all project may use it)"),
    )
    name = models.CharField(max_length=256, verbose_name=_("Project Column Set Name"))
    default_column_name = models.CharField(
        max_length=256,
        default="planning",
        verbose_name=_("Task default column name (Used when project column position is not known)"),
    )
    created_datetime = models.DateTimeField(auto_now_add=True, editable=False)
    updated_datetime = models.DateTimeField(auto_now=True, editable=False)
    label_category_prefixes = models.JSONField(
        null=True,
        blank=True,
        default=category_prefixes_default,
        help_text=_("Github Issue Labels Category Prefixes"),
    )
    label_estimate_prefixes = models.JSONField(
        null=True,
        blank=True,
        default=estimate_prefixes_default,
        help_text=_("Github Issue Labels Estimate Prefixes"),
    )

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.name})"

    def get_column_names(self):
        column_names = [c.name for c in ProjectColumn.objects.filter(columnset=self).order_by("index")]
        if self.default_column_name not in column_names:
            raise ValueError(f"default_column_name({self.default_column_name}) not defined as column: {column_names}")
        return column_names

    def get_active_column_names(self, with_priority: bool = False) -> list[str]:
        if with_priority:
            names = [(priority, c.name) for priority, c in enumerate(ProjectColumn.objects.filter(columnset=self, is_active=True).order_by("-index"))]
        else:
            names = [c.name for c in ProjectColumn.objects.filter(columnset=self, is_active=True).order_by("index")]
        if not names:
            raise ProjectColumnSetError(f"{self} does not have any ACTIVE columns assigned!")
        return names

    def get_done_column_names(self):
        names = [c.name for c in ProjectColumn.objects.filter(columnset=self, is_done=True).order_by("index")]
        if not names:
            raise ProjectColumnSetError(f"{self} does not have any DONE columns assigned!")
        return names


class ProjectColumn(models.Model):
    columnset = models.ForeignKey(ProjectColumnSet, on_delete=models.CASCADE)
    index = models.PositiveSmallIntegerField(
        _("Column Display Index"),
        default=None,
        blank=True,
        unique=True,
        help_text=_("Github Project Column Display Index (0 start)"),
    )
    name = models.CharField(max_length=256, verbose_name=_("Project Column Display Name"))
    github_id = models.PositiveIntegerField(null=True, blank=True, help_text=_("related github column id assigned on creation"))
    is_active = models.BooleanField(default=False, help_text=_("Set to True if tasks in column are considered ACTIVE"))
    is_done = models.BooleanField(default=False, help_text=_("Set to True if tasks in column are considered DONE"))

    class Meta:
        unique_together = (("columnset", "name"), ("columnset", "index"))

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.columnset.name}-{self.name})"

    def save(self, *args, **kwargs):
        # auto-increment if blank (Consider moving to admin)
        if not self.index and ProjectColumn.objects.filter(columnset=self.columnset).exists():
            # get max value of current and increment by 1
            max_index = ProjectColumn.objects.filter(columnset=self.columnset).aggregate(Max("index"))["index__max"]
            self.index = max_index + 1
            logger.info(f"{str(self)} incrementing: {self.index}")
        super().save(*args, **kwargs)

    def clean(self):
        if self.is_active and self.is_done:
            raise ValidationError("(Invalid Configuration) Both is_active and is_done set to True!")


# Project pipeline status (kippo#36 / T09). The field is still named `phase` (verbose_name フェーズ); the
# values are the sales/delivery status. `confidence` is derived from `phase` via PHASE_CONFIDENCE (no longer
# user-editable). The old anon-project value is retired — non-projects are identified by category=="non-project".
DEFAULT_PROJECT_PHASE = "proposing-low"
VALID_PROJECT_PHASES = (
    ("keep-in-touch", "KIT"),
    ("proposing-low", _("提案(低)")),
    ("proposing-mid", _("提案(中)")),
    ("proposing-high", _("提案(高)")),
    ("verbal-order", _("口頭受注")),
    ("under-contract", _("契約稼働中")),
    ("completed", _("完了")),
    ("lost", _("失注")),
)
# phase -> confidence (確度). under-contract/completed == 100 keep the monthly-assignment confirm gate working.
PHASE_CONFIDENCE = {
    "keep-in-touch": 0,  # KIT = "keep in touch": proposal did not succeed
    "proposing-low": 30,
    "proposing-mid": 80,
    "proposing-high": 90,
    "verbal-order": 99,
    "under-contract": 100,
    "completed": 100,
    "lost": 0,
}


class KippoProjectOrganizationCategory(UserCreatedBaseModel):
    """Selectable KippoProject.category value.

    A row with ``organization=None`` is a global default (seeded from
    ``DEFAULT_KIPPOPROJECT_CATEGORIES``); a row with an organization is that
    organization's custom category. ``KippoProject.category`` is a FK here so the
    available categories can optionally be made dynamic per organization (kippo#30 / T08, T20).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.KippoOrganization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="project_categories",
        verbose_name=_("組織"),
        help_text=_("Organization this category belongs to; leave empty for a global default category"),
    )
    key = models.CharField(max_length=KIPPOPROJECT_CATEGORY_MAX_LENGTH, verbose_name=_("キー"))
    label = models.CharField(max_length=128, verbose_name=_("ラベル"))
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name=_("表示順"))
    is_active = models.BooleanField(default=True, verbose_name=_("有効"))

    class Meta:
        verbose_name = _("プロジェクトカテゴリ")
        verbose_name_plural = _("プロジェクトカテゴリ")
        ordering = ("sort_order", "key")
        constraints = (
            models.UniqueConstraint(fields=("organization", "key"), name="uniq_org_category_key"),
            models.UniqueConstraint(fields=("key",), condition=models.Q(organization__isnull=True), name="uniq_global_category_key"),
        )

    def __str__(self) -> str:
        scope = self.organization.name if self.organization_id else "global"
        return f"{self.__class__.__name__}({scope}:{self.key})"

    @classmethod
    def get_for_organization(cls, organization: KippoOrganization | None) -> QuerySet:
        """Active categories available to an organization: its own rows plus the global defaults."""
        return cls.objects.filter(is_active=True).filter(models.Q(organization=organization) | models.Q(organization__isnull=True))


def get_default_project_category():
    """PK of the global (organization=null) fallback category, for KippoProject.category's default."""
    return (
        KippoProjectOrganizationCategory.objects.filter(organization__isnull=True, key=DEFAULT_PROJECT_CATEGORY_VALUE)
        .values_list("pk", flat=True)
        .first()
    )


@reversion.register()
class KippoProject(UserCreatedBaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.KippoOrganization", on_delete=models.CASCADE, verbose_name=_("組織"))
    name = models.CharField(max_length=256, unique=True, verbose_name=_("プロジェクト名"), help_text=_("Name of the project"))
    slug = models.CharField(max_length=300, unique=True, editable=False)
    phase = models.CharField(
        max_length=150,
        default=DEFAULT_PROJECT_PHASE,
        choices=VALID_PROJECT_PHASES,
        verbose_name=_("フェーズ"),
        help_text=_("State or phase of the project"),
    )
    confidence = models.PositiveSmallIntegerField(
        default=PHASE_CONFIDENCE[DEFAULT_PROJECT_PHASE],
        editable=False,
        validators=(MaxValueValidator(100), MinValueValidator(0)),
        verbose_name=_("確度"),
        help_text=_("0-100, auto-derived from phase (read-only)"),
    )
    category = models.ForeignKey(
        "projects.KippoProjectOrganizationCategory",
        on_delete=models.PROTECT,
        related_name="projects",
        default=get_default_project_category,
        verbose_name=_("カテゴリ"),
    )
    slack_channel_name = models.CharField(
        max_length=80,
        blank=True,
        default="",
        verbose_name=_("Slack会話チャンネル名"),
        help_text=_("Conversation Channel — invite the organization's slack bot to enable channel notification"),
    )
    slack_notification_channel_name = models.CharField(
        max_length=80,
        blank=True,
        default="",
        verbose_name=_("Slack通知チャンネル名"),
        help_text=_("Notification Channel for crawler / batch / development notifications (separate from the conversation channel)"),
    )
    enable_cost_report = models.BooleanField(
        default=False,
        verbose_name=_("コストレポート有効化"),
        help_text=_("Set to True if you want to enable cost reporting to the configured slack channel"),
    )
    columnset = models.ForeignKey(
        ProjectColumnSet,
        on_delete=models.DO_NOTHING,
        verbose_name=_("カラムセット"),
        help_text=_("ProjectColumnSet to use if/when a related Github project is created through Kippo"),
    )
    project_manager = models.ForeignKey(
        "accounts.KippoUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("プロジェクトマネージャー"),
        help_text=_("Project Manager assigned to the project"),
    )
    parent_project = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="upsell_children",
        verbose_name=_("親プロジェクト"),
        help_text=_("Original (parent) project for upsell projects"),
    )
    customer = models.ForeignKey(
        "customers.KippoCustomer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="projects",
        verbose_name=_("顧客"),
        help_text=_("Customer this project is delivered for (optional)"),
    )
    is_closed = models.BooleanField(_("プロジェクト終了済み"), default=False, help_text=_("Manually set when project is complete"))
    close_comment = models.TextField(_("終了コメント"), blank=True, default="")
    display_as_active = models.BooleanField(
        _("アクティブとして表示"),
        default=True,
        help_text=_("If True, project will be included in the ActiveKippoProject List"),
    )
    display_in_project_report = models.BooleanField(
        _("プロジェクトレポートサマリ(Slack)に表示"),
        default=True,
        help_text=_("If True, project will be included in the Project Report Summary"),
    )
    github_project_html_url = models.URLField(_("GitHubプロジェクトのHTML URL"), blank=True, default="")
    github_project_api_nodeid = models.CharField(
        _("GitHubプロジェクトAPIノードID"),
        max_length=255,
        blank=True,
        default="",
    )
    allocated_staff_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("割当工数(人日)"),
        help_text=_("Estimated Staff Days needed for Project Completion"),
    )
    start_date = models.DateField(_("開始日"), null=True, blank=True, help_text=_("Date the Project requires engineering resources"))
    target_date = models.DateField(
        _("完了予定日"),
        null=True,
        blank=True,
        default=get_target_date_default,
        help_text=_("Date the Project is planned to be completed by."),
    )
    actual_date = models.DateField(
        _("完了日"),
        null=True,
        blank=True,
        help_text=_("The date the project was actually completed on (not the initial target)"),
    )
    billing_date = models.DateField(
        _("請求日"),
        null=True,
        blank=True,
        help_text=_("Date the project is billed. Defaults to the target date when left blank."),
    )
    document_folder_url = models.URLField(
        _("ドキュメント保管URL"),
        blank=True,
        default="",
        help_text=_("URL of where documents for the projects are maintained"),
    )
    docbase_tag = CommaSeparatedCharField(
        _("DocBaseタグ"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Comma-separated DocBase tags used by the crawler to fetch matching posts (e.g. 'foo,bar')"),
    )
    problem_definition = models.TextField(
        _("プロジェクト課題定義"),
        blank=True,
        default="",
        help_text=_("Define the problem that the project is set out to solve."),
    )
    survey_issued = models.BooleanField(default=False, verbose_name=_("アンケート発行済み"), help_text=_("Update when survey is issued!"))
    survey_issued_datetime = models.DateTimeField(
        null=True,
        editable=False,
        verbose_name=_("アンケート発行日時"),
        help_text=_('Updated when "survey_issued" flag is set'),
    )

    class Meta:
        verbose_name = _("プロジェクト")
        verbose_name_plural = verbose_name

    def clean(self):
        if self.actual_date and self.actual_date > timezone.now().date():
            raise ValidationError(_("Given date is in the future"))
        if self.enable_cost_report and not self.slack_channel_name:
            raise ValidationError(_("slack_channel_name is required when enable_cost_report is True!"))

    def revenue_entries(
        self,
        window_start: datetime.date | None = None,
        window_end: datetime.date | None = None,
    ) -> list[tuple[datetime.date, "Decimal"]]:
        """Revenue from the billing ledger as ``(billing_date, amount)`` tuples (kippo#31 / T12).

        The ledger is the single source of truth for project revenue regardless of contract
        billing type (delivery contracts record their single billing, monthly contracts their
        generated months), so there is no double counting. The optional window is clamped by
        month (an entry is included if its month overlaps [window_start, window_end]).
        """
        entries = self.billing_entries.all()
        if window_start:
            entries = entries.filter(billing_date__gte=first_of_month(window_start))
        if window_end:
            entries = entries.filter(billing_date__lt=first_of_next_month(window_end))
        return [(entry.billing_date, entry.amount) for entry in entries]

    def developers(self):
        from tasks.models import KippoTask

        return {
            t.assignee
            for t in KippoTask.filter(project=self, assignee__is_developer=True).exclude(
                assignee__github_login__startswith=UNASSIGNED_USER_GITHUB_LOGIN_PREFIX
            )
        }

    def get_dsearch_tag(self) -> str:
        """Return the single-line sentinel JSON tag embedding this project's id.

        Used by the internal document-search crawler to register meeting minutes
        per-project (see kiconiaworks/kippo#13).
        """
        return f'[dsearch]{{"project":"{self.id}"}}[/dsearch]'

    def get_meeting_calendar_template_url(self) -> str:
        """Return a Google Calendar event-template URL pre-filled for this project.

        The event title (``text``) is pre-filled with the project name and the description
        (``details``) carries the dsearch tag. When the organization has a ``calendar_email``
        configured it is added as a forced attendee (the ``add`` parameter) so created meeting
        minutes are discovered and collected per-project (see kiconiaworks/kippo#13).
        """
        params = [("action", "TEMPLATE"), ("text", self.name), ("details", self.get_dsearch_tag())]
        if self.organization.calendar_email:
            params.append(("add", self.organization.calendar_email))
        query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params)
        return f"https://calendar.google.com/calendar/render?{query}"

    @property
    def default_column_name(self):
        return self.columnset.default_column_name

    def get_admin_url(self):
        return f"{settings.URL_PREFIX}/admin/projects/kippoproject/{self.id}/change"

    def get_absolute_url(self):
        return f"{settings.URL_PREFIX}/projects/?slug={self.slug}"

    def get_column_names(self) -> list[str]:
        """
        Get the column names for use in github project columns
        :return: Column names in expected order
        """
        if not self.columnset:
            translated_text = _("not defined!")
            raise ValueError(f"{self}.columnset {translated_text}")
        return self.columnset.get_column_names()

    def get_active_column_names(self) -> list[str]:
        if not self.columnset:
            translated_text = _("not defined!")
            raise ValueError(f"{self}.columnset {translated_text}")
        return self.columnset.get_active_column_names()

    def get_latest_kippoprojectstatus(self):
        try:
            latest_kippoprojectstatus = KippoProjectStatus.objects.filter(project=self).latest("created_datetime")
        except KippoProjectStatus.DoesNotExist:
            latest_kippoprojectstatus = None
        return latest_kippoprojectstatus

    @property
    def allocated_effort_hours(self) -> int | None:
        if self.allocated_staff_days and self.organization.day_workhours:
            return self.allocated_staff_days * self.organization.day_workhours
        logger.warning(
            f"Project.allocated_staff_days and/or Project.organization.day_workhours not set: project={self}, organization={self.organization}"
        )
        return None

    def get_projecteffort_values(self) -> tuple[int, int | None, float | None]:
        actual_effort_hours = self.get_total_effort()
        total_effort_percentage = None
        if actual_effort_hours and self.allocated_effort_hours:
            total_effort_percentage = (actual_effort_hours / self.allocated_effort_hours) * 100
        return actual_effort_hours, self.allocated_effort_hours, total_effort_percentage

    def get_expected_effort(self, at_date: datetime.date | None = None) -> tuple[int | None, int | None]:
        """Calculate the expected effort hours for the project at a given date"""
        expected_effort_days = None
        expected_effort_hours = None
        if self.start_date and self.target_date and self.allocated_staff_days:
            if not at_date:
                at_date = timezone.localdate()
                logger.info(f"at_date not given, setting to: {at_date}")
            if self.start_date <= at_date <= self.target_date or at_date > self.target_date:
                total_project_hours = self.allocated_staff_days * self.organization.day_workhours
                # get weekdays - public holidays
                holidays = []
                if self.organization.default_holiday_country:
                    holidays = list(
                        PublicHoliday.objects.filter(
                            country=self.organization.default_holiday_country, day__gte=self.start_date, day__lte=self.target_date
                        ).values_list("day", flat=True)
                    )

                total_available_workdays = 0
                available_workdays_at_date = None
                current_date = self.start_date
                saturday_weekday_number = 5
                while current_date <= self.target_date:
                    if current_date.weekday() < saturday_weekday_number and current_date not in holidays:
                        total_available_workdays += 1
                    if current_date == at_date:
                        available_workdays_at_date = total_available_workdays
                    current_date = current_date + timezone.timedelta(days=1)
                if available_workdays_at_date is None and at_date > self.target_date:
                    logger.warning(f"at_date({at_date}) > target_date({self.target_date}), setting available_workdays_at_date")
                    # set to total available workdays
                    available_workdays_at_date = total_available_workdays

                if total_available_workdays:
                    hours_per_day = total_project_hours / total_available_workdays
                    expected_effort_hours = hours_per_day * available_workdays_at_date
            else:
                logger.warning(f"at_date({at_date}) is not between start_date({self.start_date}) and target_date({self.target_date})")
        else:
            logger.warning(
                f"Project.start_date, Project.target_date and/or Project.allocated_staff_days not set: "
                f"project={self}, organization={self.organization}"
            )
            logger.warning(f"start_date={self.start_date}, target_date={self.target_date}, allocated_staff_days={self.allocated_staff_days}")
        return expected_effort_days, expected_effort_hours

    def get_projectprogressstatus_values(self) -> ProjectProgressStatus:
        actual_effort_hours, allocated_effort_hours, total_effort_percentage = self.get_projecteffort_values()
        expected_effort_days, expected_effort_hours = self.get_expected_effort()
        logger.debug(
            f"project={self.name}, allocated_effort_hours={allocated_effort_hours}, "
            f"actual_effort_hours={actual_effort_hours}, expected_effort_hours={expected_effort_hours}"
        )
        project_progress_status = ProjectProgressStatus(
            project=self,
            date=timezone.localdate(),
            current_effort_hours=actual_effort_hours,
            expected_effort_hours=expected_effort_hours,
            expected_effort_days=expected_effort_days,
            allocated_effort_hours=allocated_effort_hours,
            allocated_effort_days=self.allocated_staff_days,
        )
        return project_progress_status

    def get_projecteffort_display(self) -> str:
        result = "-"
        total_effort_percentage_str = ""
        actual_effort_hours, allocated_effort_hours, total_effort_percentage = self.get_projecteffort_values()
        if total_effort_percentage:
            total_effort_percentage_str = f" ({total_effort_percentage:.2f}%)"
        if actual_effort_hours:
            result = f"{actual_effort_hours}h{total_effort_percentage_str}"
        return result

    def get_weekly_kippoprojectstatus_entries(self, week_start_datetime: datetime.date | None = None) -> QuerySet:
        if not week_start_datetime:
            week_start_date = previous_week_startdate()
            time_deadline = self.organization.weekly_project_time_deadline
            week_start_datetime = datetime.datetime.combine(week_start_date, time_deadline, tzinfo=settings.JST)

        week_end_datetime = week_start_datetime + datetime.timedelta(days=7)
        assert week_start_datetime.tzinfo is not None, "week_start_datetime must be timezone-aware"
        assert week_end_datetime.tzinfo is not None, "week_end_datetime must be timezone-aware"

        # get the latest KippoProjectStatus for the given week
        logger.debug(f"Collecting KippoProjectStatus entries for week: {week_start_datetime} ({week_start_datetime} - {week_end_datetime})")
        entries = KippoProjectStatus.objects.filter(
            project=self, created_datetime__gte=week_start_datetime, created_datetime__lt=week_end_datetime
        ).order_by(
            # uniquely identify the users with same name, username should be different (but want to orderby the last name)
            "created_by__last_name",
            "created_by__username",
            "created_datetime",
        )
        logger.debug(f"{self.name} len(weekly_kippoprojectstatus_entries)={len(entries)}")
        return entries

    def get_active_taskstatus(
        self, max_effort_date: datetime.date | None = None, additional_filters: dict[str, Any] | None = None
    ) -> tuple[list[KippoTaskStatus], bool]:
        """Get the latest KippoTaskStatus entries for active tasks for the given Project(s)"""
        has_estimates = False
        valid_column_states = self.get_active_column_names() + ["open"]
        qs = KippoTaskStatus.objects.filter(task__github_issue_api_url__isnull=False, task__project=self)  # filter out non-linked tasks
        if additional_filters:
            logger.debug(f"additional_filters={additional_filters}")
            qs = qs.filter(**additional_filters)

        if max_effort_date:
            qs = qs.filter(effort_date__lte=max_effort_date)
        results = qs.order_by("task__github_issue_api_url", "-effort_date").distinct("task__github_issue_api_url")

        # only include active states
        taskstatus_results = [r for r in list(results) if r.state in valid_column_states]
        if any(status.estimate_days for status in taskstatus_results):
            has_estimates = True
        return taskstatus_results, has_estimates

    def get_latest_taskstatuses(self, current_date: datetime.date | None = None, active_only: bool = False) -> QuerySet:  # KippoTaskStatus
        """Get the latest KippoTaskStatus entries for active tasks for the given Project(s)"""
        if not current_date:
            current_date = timezone.now().date()

        target_kippotaskstatus_ids = (
            KippoTaskStatus.objects.filter(
                task__github_issue_api_url__isnull=False,
                task__project=self,
                effort_date__lte=current_date,  # filter out non-linked tasks
            )
            .order_by("task__github_issue_api_url", "-effort_date")
            .distinct("task__github_issue_api_url")
            .values_list("pk", flat=True)
        )

        # filter by active columns and get desired values
        valid_column_states = self.get_column_names()
        if active_only:
            valid_column_states = self.get_active_column_names() + ["open"]

        status_entries = KippoTaskStatus.objects.filter(pk__in=target_kippotaskstatus_ids, state__in=valid_column_states)
        return status_entries

    def get_projectsurvey_url(self):
        """Generate and return the project survey URL pre-populated with project-id"""
        url = ""
        if self.organization.google_forms_project_survey_url and self.organization.google_forms_project_survey_projectid_entryid:
            params = {
                "usp": "pp_url",  # not sure what this is (pre-populated url?)
                self.organization.google_forms_project_survey_projectid_entryid: self.id,
            }
            encoded_params = urlencode(params)
            url = f"{self.organization.google_forms_project_survey_url}?{encoded_params}"
        return url

    def active_milestones(self):
        today = timezone.now().date()
        return KippoMilestone.objects.filter(project=self, target_date__gte=today).order_by("-target_date")

    def related_github_repositories(self) -> QuerySet:
        """Returns octocat.GithubRepository objects attached to this project."""
        from octocat.models import GithubRepository

        # get kippotask github_repository_html_url
        from tasks.models import KippoTask

        # get related repositories through the KippoTask(s) attached to the KippoProject
        # Includes both formats:
        # -- {repository_url}
        # -- {repository_url}/
        repository_html_urls = set()
        for issue_html_url in KippoTask.objects.filter(project=self).values_list("github_issue_html_url", flat=True):
            logger.debug(f"issue_html_url={issue_html_url}")
            root_repository_url = issue_html_url.rsplit("/", 2)[0]
            # add root
            repository_html_urls.add(root_repository_url)
            # add with
            repository_html_url = f"{root_repository_url}/"
            repository_html_urls.add(repository_html_url)
        return GithubRepository.objects.filter(html_url__in=tuple(repository_html_urls))

    def get_total_effort(self) -> int:
        result = 0
        total_effort_hours = ProjectWeeklyEffort.objects.filter(project=self).aggregate(Sum("hours"))
        if total_effort_hours and "hours__sum" in total_effort_hours:
            result = total_effort_hours["hours__sum"]
        return result

    @property
    def github_project_name(self):
        return self.name

    @property
    def github_project_description(self):
        project_manager_display_name = ""
        if self.project_manager:
            project_manager_display_name = self.project_manager.display_name
        description = (
            f"""project_manager: {project_manager_display_name}<br/>"""
            f"""start_date: {self.start_date}                       <br/>"""
            f"""end_date  : {self.target_date}                      <br/>"""
        )
        return description

    def save(self, *args, **kwargs):
        if self.survey_issued and not self.survey_issued_datetime:
            self.survey_issued_datetime = timezone.now()

        if self.is_closed and not self.closed_datetime:
            self.closed_datetime = timezone.now()
        elif not self.is_closed and self.closed_datetime:
            self.closed_datetime = None

        if not self.billing_date and self.target_date:
            self.billing_date = self.target_date

        # confidence (確度) is derived from phase — never user-set (kippo#36 / T09)
        self.confidence = PHASE_CONFIDENCE.get(self.phase, self.confidence)

        if self._state.adding:  # created
            # perform initial creation tasks
            self.slug = slugify(self.name, allow_unicode=True)

        # Slack's slash-command payload sends "channel_name" without a leading "#",
        # so the slash-command project lookup ("...subcommands/projectstatus.py") does
        # an exact string match on the bare name. Store the canonical bare form here
        # so users entering "#proj-foo" or "  proj-foo  " in admin still resolve.
        self.slack_channel_name = _normalize_slack_channel_name(self.slack_channel_name)
        self.slack_notification_channel_name = _normalize_slack_channel_name(self.slack_notification_channel_name)

        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.name})"


class ActiveKippoProjectManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()

        # update so only 'active"
        qs = qs.filter(is_closed=False, display_as_active=True)
        return qs


class ActiveKippoProject(KippoProject):
    objects = ActiveKippoProjectManager()

    class Meta:
        proxy = True
        verbose_name = _("プロジェクト(実行中)")
        verbose_name_plural = verbose_name


class KippoProjectContract(UserCreatedBaseModel):
    """The agreed billing terms for a project (kippo#31 / T11) — *how* the project is billed.

    Terms live here rather than on ``KippoProject`` so a delivery project never carries a
    meaningless monthly field, and renewals/amendments are additional rows instead of
    overwrites. Billing *events* live in the ``KippoProjectBillingEntry`` ledger, generated
    from these terms via ``generate_billing_entries()``.
    """

    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, related_name="contracts")
    billing_type = models.CharField(
        _("請求方法"),
        max_length=20,
        choices=VALID_BILLING_TYPES,
        default=DEFAULT_BILLING_TYPE,
        help_text=_("'delivery' (納品, amount billed once at the contract end_date) or 'monthly' (月額, amount accrues month-end per month)."),
    )
    amount = models.DecimalField(
        _("金額"),
        max_digits=12,
        decimal_places=0,
        help_text=_("JPY. Contract total for 'delivery'; per-month amount for 'monthly'."),
    )
    start_date = models.DateField(
        _("契約開始日"),
        null=True,
        blank=True,
        help_text=_("Contract period start. Auto-populated from the project start_date when left blank."),
    )
    end_date = models.DateField(
        _("契約終了日"),
        null=True,
        blank=True,
        help_text=_("Contract period end. Auto-populated from the project target_date when left blank."),
    )
    note = models.CharField(
        _("備考"),
        max_length=255,
        blank=True,
        default="",
    )

    class Meta:
        verbose_name = _("契約")
        verbose_name_plural = verbose_name
        ordering = ("created_datetime",)

    def __str__(self) -> str:
        return f"KippoProjectContract({self.project.name} {self.billing_type} ¥{self.amount})"

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError(_("Contract start_date is after end_date"))

    def save(self, *args, **kwargs):
        # auto-populate the contract period from the project when left blank
        if not self.start_date:
            self.start_date = self.project.start_date
        if not self.end_date:
            self.end_date = self.project.target_date
        super().save(*args, **kwargs)

    def generate_billing_entries(self, created_by: KippoUser | None = None) -> list["KippoProjectBillingEntry"]:
        """Populate the project's billing ledger from these terms (kippo#31 / T12).

        - monthly: one entry (dated the last day of the month, amount=``amount``) per calendar
          month the contract period [start_date, end_date] overlaps — Japanese month-end (月末)
          billing. A month is included if any part of it falls within the period; the full
          amount accrues for each such month (no proration — adjust the individual entry
          afterwards if proration is needed).
        - delivery: one entry of ``amount`` at the contract ``end_date`` (which itself
          auto-populates from the project target_date).

        Idempotent: dates that already have an entry (including manually adjusted ones) are
        left untouched. Returns only the newly created entries. Returns [] when the dates
        needed for the billing_type are not resolvable.
        """
        existing_dates = set(self.project.billing_entries.values_list("billing_date", flat=True))
        missing_entries = []

        if self.billing_type == BILLING_TYPE_MONTHLY:
            if not self.start_date or not self.end_date:
                return []
            current = first_of_month(self.start_date)
            end = first_of_month(self.end_date)
            while current <= end:
                entry_date = last_of_month(current)
                if entry_date not in existing_dates:
                    missing_entries.append(self._build_entry(entry_date, created_by))
                current = first_of_next_month(current)
        else:  # delivery
            delivery_date = self.end_date
            if not delivery_date:
                return []
            if delivery_date not in existing_dates:
                missing_entries.append(self._build_entry(delivery_date, created_by))

        return KippoProjectBillingEntry.objects.bulk_create(missing_entries)

    def _build_entry(self, billing_date: datetime.date, created_by: KippoUser | None) -> "KippoProjectBillingEntry":
        return KippoProjectBillingEntry(
            project=self.project,
            contract=self,
            billing_date=billing_date,
            amount=self.amount,
            created_by=created_by,
            updated_by=created_by,
        )


class KippoProjectBillingEntry(UserCreatedBaseModel):
    """A single billing/revenue entry in a project's billing ledger (kippo#31 / T11, T12).

    The ledger is the single source of truth for project revenue. Entries are generated from
    the project's ``KippoProjectContract`` terms (one entry for a delivery contract, one per
    month for a monthly contract) and individual entries can then be adjusted (price revision,
    proration) or added manually without touching the contract.
    """

    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, related_name="billing_entries")
    contract = models.ForeignKey(
        KippoProjectContract,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_entries",
        help_text=_("Contract the entry was generated from (blank for manually added entries)."),
    )
    billing_date = models.DateField(
        _("請求日"),
        help_text=_("Date the entry is billed/recognized. Monthly-generated entries use the month-end (月末) date."),
    )
    amount = models.DecimalField(
        _("金額"),
        max_digits=12,
        decimal_places=0,
        help_text=_("Billed amount (JPY)."),
    )
    note = models.CharField(
        _("備考"),
        max_length=255,
        blank=True,
        default="",
    )

    class Meta:
        verbose_name = _("請求エントリ")
        verbose_name_plural = verbose_name
        ordering = ("billing_date",)
        constraints = (models.UniqueConstraint(fields=("project", "billing_date"), name="unique_billingentry_project_billing_date"),)

    def __str__(self) -> str:
        return f"KippoProjectBillingEntry({self.project.name} {self.billing_date} ¥{self.amount})"


class KippoProjectStatus(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE)
    comment = models.TextField(help_text=_("Current Status"))

    def __str__(self) -> str:
        return f"ProjectStatus({self.project.name} {self.created_datetime})"


@reversion.register()
class KippoMilestone(UserCreatedBaseModel):
    """Provides milestone definition and mapping to a Github Repository Milestone"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, verbose_name=_("Kippo Project"), editable=False)
    title = models.CharField(max_length=256, verbose_name=_("Title"))
    number = models.PositiveSmallIntegerField(editable=False, help_text=_("Internal Per Project Management Number"))
    allocated_staff_days = models.PositiveSmallIntegerField(null=True, blank=True, help_text=_("Budget Allocated Staff Days"))
    is_completed = models.BooleanField(_("Is Completed"), default=False)
    start_date = models.DateField(_("Start Date"), null=True, blank=True, default=None, help_text=_("Milestone Start Date"))
    target_date = models.DateField(_("Target Date"), null=True, blank=True, default=None, help_text=_("Milestone Target Completion Date"))
    actual_date = models.DateField(_("Actual Date"), null=True, blank=True, default=None, help_text=_("Milestone Actual Completion Date"))
    description = models.TextField(_("Description"), blank=True, default="", help_text=_("Describe the purpose of the milestone"))

    class Meta:
        verbose_name = _("マイルストーン")
        verbose_name_plural = verbose_name
        unique_together = ("project", "start_date", "target_date")

    @property
    def github_state(self) -> str:
        """
        Mapping of KippoMilestone is_completed to github milestone state value
        Github valid states are: open, closed, or all
        https://developer.github.com/v3/issues/milestones/

        :return: ('open'|'closed')
        """
        return "open" if not self.is_completed else "closed"

    def clean(self):
        if self.actual_date and (self.actual_date > timezone.now().date()):
            raise ValidationError(_("Given date is in the future"))

        # check start/target date
        if (self.start_date and self.target_date) and self.target_date < self.start_date:
            raise ValidationError(f"start_date({self.start_date}) > target_date({self.target_date})")

    def get_absolute_url(self):
        return f"{settings.URL_PREFIX}/admin/projects/kippomilestone/{self.id}/change/"

    def get_url(self):
        """Url for non-admin page"""
        return f"{settings.URL_PREFIX}/projects/milestones/{self.id}/"

    @property
    def is_delayed(self):
        return not self.is_completed and not self.actual_date and self.target_date and self.target_date < timezone.now().date()

    @property
    def estimated_completion_date(self) -> datetime.date | None:
        from tasks.functions import get_projects_load, get_ttlhash

        # project_developer_load
        # {'PROJECT_ID':  # multiple
        #     {
        #         'GITHUB_LOGIN': [
        #             KippoTask(),  # with 'qlu_task' attribute with scheduled QluTask object
        #             KippoTask()  # with 'qlu_task' attribute with scheduled QluTask object
        #                 ...
        #         ]
        #     },
        # }
        project_developer_load, _, _ = get_projects_load(organization=self.project.organization, ttl_hash=get_ttlhash(seconds=60))

        # retrieve the number of estimated days assigned to this milestone
        max_effort_date = None
        milestone_scheduled_effort_dates = []
        for project_id, project_task_data in project_developer_load.items():
            if project_id != self.project.id:
                logger.debug(f"project_id({project_id}) != milestone.project.id({self.project.id})")
                continue
            for user_assigned_tasks in project_task_data.values():
                for task in user_assigned_tasks:
                    if task.milestone == self:
                        # get assigned dates
                        for date in task.qlu_task.get_scheduled_dates():
                            logger.debug(f"scheduled task({task.title}) date: {date}")
                            milestone_scheduled_effort_dates.append(date)
        if milestone_scheduled_effort_dates:
            max_effort_date = max(milestone_scheduled_effort_dates)
        return max_effort_date

    def get_assignee_workdays(self, start_date: datetime.date | None = None) -> Counter:
        if not start_date:
            current_datetime = timezone.now()
            # TODO: review -- this was set to day = 1... not sure if there was a specific reason for that
            start_datetime = datetime.datetime(current_datetime.year, current_datetime.month, current_datetime.day, tzinfo=datetime.UTC)
            start_date = start_datetime.date()
            start_date = max(start_date, self.start_date)

        # get organization memberships
        organization_memberships = list(
            OrganizationMembership.objects.filter(organization=self.project.organization, user__github_login__isnull=False, is_developer=True)
            .exclude(user__github_login__startswith=settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX)
            .order_by("user__github_login")
        )
        member_personal_holiday_dates = {m.user.github_login: tuple(m.user.personal_holiday_dates()) for m in organization_memberships}
        member_public_holiday_dates = {m.user.github_login: tuple(m.user.public_holiday_dates()) for m in organization_memberships}

        # initialize counter for organization_memberships to zero (0)
        assignee_available_workdays = Counter({m.user: 0 for m in organization_memberships})
        current_date = start_date
        while current_date <= self.target_date:
            for membership in organization_memberships:
                if (
                    current_date not in member_personal_holiday_dates[membership.user.github_login]
                    and current_date not in member_public_holiday_dates[membership.user.github_login]
                    and current_date.weekday() in membership.committed_weekdays
                ):
                    assignee_available_workdays[membership.user] += 1
            current_date += datetime.timedelta(days=1)
        return assignee_available_workdays

    @property
    def assignee_available_workdays(self) -> str:
        assignee_available_workdays = self.get_assignee_workdays()
        return ", ".join(f"{assignee}={workdays}" for assignee, workdays in assignee_available_workdays.items())

    def available_work_days(self, start_date: datetime.date | None = None) -> int:
        """Calculated the work days available considering the FULL OrganizationMembership available assignments"""
        assignee_available_workdays = self.get_assignee_workdays(start_date)
        total_available_workdays = sum(assignee_available_workdays.values())
        return total_available_workdays

    @property
    def estimated_work_days(self) -> int:
        """Return the effort days assigned to tasks in the given milestone"""
        # retrieve the number of estimated days assigned to this milestone
        assignee_estimated_workdays = self.get_assignee_estimated_workdays()
        total_assignee_estimated_workdays = sum(assignee_estimated_workdays.values())
        return total_assignee_estimated_workdays

    def get_assignee_task_counts(self) -> Counter:
        assignee_task_counts = Counter()
        active_task_states = self.project.columnset.get_active_column_names()
        results = KippoTaskStatus.objects.filter(task__in=self.tasks).order_by("task", "-effort_date").distinct("task")
        for r in results:
            if r.state in active_task_states:
                assignee_task_counts[r.task.assignee] += 1
        return assignee_task_counts

    def get_assignee_estimated_workdays(self) -> Counter:
        assignee_estimated_workdays = Counter()
        active_task_states = self.project.columnset.get_active_column_names()
        results = KippoTaskStatus.objects.filter(task__in=self.tasks).order_by("task", "-effort_date").distinct("task")
        for r in results:
            if r.state in active_task_states:
                logger.info(f"adding estimate {r} estimate_days={r.estimate_days}")
                estimate_days = settings.FALLBACK_ESTIMATE_DAYS
                if r.estimate_days:
                    estimate_days = r.estimate_days
                else:
                    logger.warning(f"{r} estimate_days is None, using settings.FALLBACK_ESTIMATE_DAYS={settings.FALLBACK_ESTIMATE_DAYS}")
                assignee = r.task.assignee
                assignee_estimated_workdays[assignee] += estimate_days
        return assignee_estimated_workdays

    @property
    def tasks(self) -> QuerySet:
        return self.kippotask_milestone.order_by("assignee")  # reverse relation to KippoTask

    @property
    def active_tasks(self) -> QuerySet:
        active_task_states = self.project.columnset.get_active_column_names()
        task_ids = (
            KippoTaskStatus.objects.filter(task__in=self.tasks).order_by("task", "-effort_date").distinct("task").values_list("state", "task__id")
        )
        # filter out non-active tasks
        active_task_ids = [task_id for task_state, task_id in task_ids if task_state in active_task_states]
        results = self.kippotask_milestone.filter(pk__in=active_task_ids).order_by("assignee")
        return results

    def update_github_milestones(self, user: KippoUser | None = None, close: bool = False) -> list[tuple[bool, object]]:
        """
        Create or Update related github milestones belonging to github repositories attached to the related project.
        :return:
            .. code:: python
                [
                    (CREATED, GithubMilestone Object),
                ]
        """
        from octocat.models import GITHUB_MILESTONE_CLOSE_STATE, GithubMilestone

        github_milestones = []
        if not user:
            logger.warning(f"user object not given, using: {GITHUB_MANAGER_USERNAME}")
            user = KippoUser.objects.get(username=GITHUB_MANAGER_USERNAME)

        # collect existing
        existing_github_milestones_by_repo_html_url = {}
        existing_github_repositories_by_html_url = {}
        for github_repository in self.project.related_github_repositories():
            url = github_repository.html_url
            url = url.removesuffix("/")
            existing_github_repositories_by_html_url[url] = github_repository
            for github_milestone in GithubMilestone.objects.filter(repository=github_repository, milestone=self):
                existing_github_milestones_by_repo_html_url[url] = github_milestone

        github_organization_name = self.project.organization.github_organization_name
        token = self.project.organization.githubaccesstoken.token
        manager = GithubOrganizationManager(organization=github_organization_name, token=token)

        # identify related github project and get related repository urls
        related_repository_html_urls = list(existing_github_repositories_by_html_url.keys())
        if not related_repository_html_urls:
            logger.warning(f"Related Repository URLS not found for KippoProject: {self.project.name}")
        else:
            for repository in manager.repositories():
                if repository.html_url in related_repository_html_urls:
                    logger.info(f"Updating {repository.name} Milestones...")
                    created = False
                    github_state = self.github_state
                    if close:
                        github_state = GITHUB_MILESTONE_CLOSE_STATE
                    if repository.html_url in existing_github_milestones_by_repo_html_url:
                        github_milestone = existing_github_milestones_by_repo_html_url[repository.html_url]
                        logger.debug(f"Updating Existing Github Milestone({self.title}) for Repository({repository.name}) ...")
                        repository.update_milestone(
                            title=self.title,
                            description=self.description,
                            due_on=self.target_date,
                            state=github_state,
                            number=github_milestone.number,
                        )
                        # mark as updated
                        github_milestone.updated_by = user
                        github_milestone.save()
                    else:
                        logger.debug(f"Creating NEW Github Milestone for Repository({repository.name}) ...")
                        response = repository.create_milestone(
                            title=self.title, description=self.description, due_on=self.target_date, state=github_state
                        )

                        # get number and create GithubMilestone entry
                        # milestone_content defined at:
                        # https://developer.github.com/v3/issues/milestones/#create-a-milestone
                        status_code, milestone_content = response
                        if status_code == UNPROCESSABLE_ENTITY_422:
                            # indicates milestone already exists on github
                            logger.warning(
                                f"422 response from github, milestone may already exist for repository({repository.name}): {milestone_content}"
                            )
                            continue

                        number = milestone_content["number"]
                        api_url = milestone_content["url"]
                        html_url = milestone_content["html_url"]
                        github_repository = existing_github_repositories_by_html_url[repository.html_url]
                        github_milestone = GithubMilestone(
                            milestone=self,
                            created_by=user,
                            updated_by=user,
                            number=number,
                            repository=github_repository,
                            api_url=api_url,
                            html_url=html_url,
                        )
                        github_milestone.save()
                        created = True
                    action = "create" if created else "update"
                    logger.info(f"+ {action} Github Milestone: ({repository.name}) {self.title}")
                    github_milestones.append((created, github_milestone))
        return github_milestones

    def save(self, *args, **kwargs):
        if self._state.adding:  # created
            # assign project number
            existing_milestone_count = KippoMilestone.objects.filter(project=self.project).count()
            if existing_milestone_count > 1:
                # Milestones may be deleted, make sure to use a number that is not in use
                # use existing max number + 1
                max_project_number = KippoMilestone.objects.filter(project=self.project).aggregate(Max("number"))["number__max"]
                self.number = max_project_number + 1
            else:
                self.number = 0

        # auto-update is_completed field if actual_date is entered
        if self.actual_date and self.actual_date < timezone.now().date():
            self.is_completed = True

        # auto-set actual date if complete is set and actual not defined
        if self.is_completed and not self.actual_date:
            self.actual_date = timezone.now().date()
        elif not self.is_completed and self.actual_date:
            # clear set date if is_completed returns to False
            self.actual_date = None

        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.title})"


@receiver(pre_delete, sender=KippoMilestone)
def cleanup_github_milestones(sender: type[KippoMilestone], instance: KippoMilestone, **kwargs):  # noqa: ARG001
    """Close related Github milestones when  KippoMilestone is deleted."""
    from octocat.models import GithubMilestone

    try:
        related_github_milestones = GithubMilestone.objects.filter(milestone=instance).exists()
        if related_github_milestones:
            instance.update_github_milestones(close=True)
    except GithubMilestone.DoesNotExist:
        logger.info("no related GithubMilestone, will not attempt to close on github")


class ProjectMonthlyAssignment(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.DO_NOTHING, related_name="projectassignment_project")
    user = models.ForeignKey("accounts.KippoUser", on_delete=models.DO_NOTHING, related_name="projectassignment_user")
    month = models.DateField(null=True, blank=True, help_text=_("Assignment month (defaults to project start_date month)"))
    is_confirmed = models.BooleanField(default=False, help_text=_("Assignment is confirmed or not"))
    percentage = models.SmallIntegerField(
        help_text=_("Workload percentage assigned to project from available workload available for project organization")
    )

    class Meta:
        unique_together = ("project", "user", "month")

    def clean(self) -> None:
        super().clean()
        # Default month to the project's start_date month (first day of month)
        if not self.month and self.project and self.project.start_date:
            self.month = self.project.start_date.replace(day=1)

        # Validate that user is a member of the project's organization
        if self.user and self.project:
            is_member = OrganizationMembership.objects.filter(user=self.user, organization=self.project.organization).exists()
            if not is_member:
                raise ValidationError(
                    _("User '%(user)s' is not a member of the project's organization '%(org)s'"),
                    params={"user": self.user, "org": self.project.organization},
                    code="invalid_user_organization",
                )

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)
        # Warn (do not block) when the user's total percentage for the organization in this
        # month exceeds 100%. Over-allocation is permitted; UI / admin surfaces are
        # responsible for visually displaying the warning to end users.
        if self.user and self.project and self.month:
            total_percentage = (
                ProjectMonthlyAssignment.objects.filter(
                    user=self.user,
                    project__organization=self.project.organization,
                    month=self.month,
                ).aggregate(total=Sum("percentage"))["total"]
                or 0
            )
            if total_percentage > MAX_ASSIGNMENT_PERCENTAGE:
                logger.warning(
                    "User '%s' has total assignment of %d%% for organization '%s' in month %s (exceeds 100%%)",
                    self.user,
                    total_percentage,
                    self.project.organization,
                    self.month.strftime("%Y-%m"),
                )


class ProjectWeeklyEffort(UserCreatedBaseModel):
    week_start = models.DateField(default=previous_week_startdate, help_text="Effort Week Start (MONDAY)")
    project = models.ForeignKey(KippoProject, on_delete=models.DO_NOTHING, related_name="projectweeklyeffort_project")
    user = models.ForeignKey("accounts.KippoUser", on_delete=models.DO_NOTHING, related_name="projectweeklyeffort_user")
    hours = models.SmallIntegerField(
        validators=(MinValueValidator(0), MaxValueValidator(7 * 24)),
        help_text=_("Actual effort in hours performed on the project for the given 'week start' (0-168)"),
    )

    class Meta:
        verbose_name = _("プロジェクト週間稼働量")
        verbose_name_plural = verbose_name
        unique_together = ("week_start", "project", "user")


class CollectIssuesAction(UserCreatedBaseModel):
    start_datetime = models.DateTimeField(default=timezone.now)
    end_datetime = models.DateTimeField(null=True, default=None)
    organization = models.ForeignKey("accounts.KippoOrganization", on_delete=models.CASCADE)

    @property
    def status(self):
        total_count = CollectIssuesProjectResult.objects.filter(action=self).count()
        completed_count = CollectIssuesProjectResult.objects.filter(action=self, state="complete").count()
        if total_count:
            percentage = round((completed_count / total_count) * 100, 2)
            result = f"{completed_count}/{total_count} {percentage}%"
        else:
            result = "0/0 0.00%"
        return result

    @property
    def new_task_count(self):
        sum_result = CollectIssuesProjectResult.objects.filter(action=self).aggregate(Sum("new_task_count"))
        result = 0
        if sum_result:
            result = sum_result.get("new_taskstatus_count__sum", 0)
        return result

    @property
    def new_taskstatus_count(self):
        sum_result = CollectIssuesProjectResult.objects.filter(action=self).aggregate(Sum("new_taskstatus_count"))
        result = 0
        if sum_result:
            result = sum_result.get("new_taskstatus_count__sum", 0)
        return result

    @property
    def updated_taskstatus_count(self):
        sum_result = CollectIssuesProjectResult.objects.filter(action=self).aggregate(Sum("updated_taskstatus_count"))
        result = 0
        if sum_result:
            result = sum_result.get("new_taskstatus_count__sum", 0)
        return result

    def save(self, *args, **kwargs):
        total_count = CollectIssuesProjectResult.objects.filter(action=self).count()
        completed_count = CollectIssuesProjectResult.objects.filter(action=self, state="complete").count()
        if total_count and completed_count == total_count:
            self.end_datetime = timezone.now()
        super().save(*args, **kwargs)


VALID_COLLECTISSUESPROJECTRESULT_STATES = (("processing", "processing"), ("complete", "complete"))


class CollectIssuesProjectResult(models.Model):
    action = models.ForeignKey(CollectIssuesAction, on_delete=models.CASCADE)
    project = models.ForeignKey("projects.KippoProject", on_delete=models.CASCADE)
    state = models.CharField(max_length=10, choices=VALID_COLLECTISSUESPROJECTRESULT_STATES, default="processing")
    new_task_count = models.PositiveSmallIntegerField(default=0)
    new_taskstatus_count = models.PositiveSmallIntegerField(default=0)
    updated_taskstatus_count = models.PositiveSmallIntegerField(default=0)
    unhandled_issues = models.JSONField()


class KippoProjectUserStatisfactionResult(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE)
    SCORE_CHOICES = ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5))
    fullfillment_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name=_("充実した時間"))
    growth_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name=_("成長"))

    class Meta:
        verbose_name = _("振り返り従業員アンケート")
        verbose_name_plural = verbose_name
        unique_together = ("project", "created_by")

    def __str__(self, *args, **kwargs) -> str:
        return f"{self._meta.verbose_name} {self.project.name} {self.created_by.display_name}"


def get_current_month() -> datetime.date:
    return timezone.now().replace(day=1).date()


class KippoProjectUserMonthlyStatisfactionResult(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE)
    date = models.DateField(default=get_current_month)
    SCORE_CHOICES = ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5))
    fullfillment_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name=_("充実した時間"))
    growth_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name=_("成長"))

    class Meta:
        verbose_name = _("（月）従業員アンケート")
        verbose_name_plural = verbose_name
        unique_together = ("created_by", "project", "date")

    def __str__(self, *args, **kwargs) -> str:
        return f"{self._meta.verbose_name} {self.project.name} ({self.date.strftime('%Y-%m')}) {self.created_by.display_name}"


class ProjectAssignmentRate(UserCreatedBaseModel):
    """Daily rate configuration per role for a project."""

    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, related_name="assignment_rates")
    role = models.CharField(max_length=50, choices=ProjectRoles.choices())
    rate_per_day = models.PositiveIntegerField(default=settings.DEFAULT_PROJECT_DAILY_RATE)

    class Meta:
        verbose_name = _("Project Assignment Rate")
        verbose_name_plural = _("Project Assignment Rates")
        unique_together = ("project", "role")

    def __str__(self) -> str:
        return f"{self.project.name} - {self.role}: {self.rate_per_day}"


class ProjectMonthlyCost(TimestampedModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE)
    month = models.DateField(null=True, blank=True, help_text=_("COST month (defaults to project start_date month)"))
    service = models.CharField(max_length=50, choices=ValidServices.choices())
    cost = models.FloatField()
    currency = models.CharField(choices=ValidCurrencies.choices(), default=ValidCurrencies.USD.value, max_length=50)
    itemized_cost = models.JSONField(null=True)  # {"item_name": {VALUE}, ...}

    def __str__(self) -> str:
        display_month = self.month.strftime("%Y-%m")
        return f"{self.project.name}({self.project.id}) [{display_month}]"
