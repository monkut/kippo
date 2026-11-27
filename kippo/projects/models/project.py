"""KippoProject, its proxies, status comments and フェーズ / 確度 change log."""

import datetime
import logging
import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from commons.fields import CommaSeparatedCharField
from commons.functions import first_of_month, first_of_next_month
from commons.models import PersistedFieldSnapshotMixin, UserCreatedBaseModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import QuerySet, Sum
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from tasks.models import KippoTaskStatus

from ..definitions import (
    BILLING_TYPE_DELIVERY,
    DEFAULT_PROJECT_CATEGORY_VALUE,
    LEAD_SOURCE_MAX_LENGTH,
    PRICING_BASIS_FIXED,
    VALID_LEAD_SOURCES,
    ProjectProgressStatus,
    ProjectRoles,
)
from ..functions import previous_week_startdate
from ..services.progress import expected_effort_hours
from .category import KippoProjectOrganizationCategory, get_default_project_category
from .columnset import ProjectColumnSet
from .definitions import (
    _COMPUTE,
    DEFAULT_PROJECT_PHASE,
    PHASE_CONFIDENCE,
    PHASE_UNDER_CONTRACT,
    SALES_PROJECT_PHASES,
    UNASSIGNED_USER_GITHUB_LOGIN_PREFIX,
    UNDER_CONTRACT_REQUIRES_CONTRACT_MSG,
    VALID_PROJECT_PHASES,
)

if TYPE_CHECKING:
    from .contract import KippoProjectContract

logger = logging.getLogger(__name__)


def get_target_date_default() -> datetime.date:
    # TODO: update to take into account configured holidays
    return (timezone.now() + timezone.timedelta(days=settings.DEFAULT_KIPPORPOJECT_TARGET_DATE_DAYS)).date()


def _normalize_slack_channel_name(value: str) -> str:
    return value.strip().lstrip("#") if value else value


class KippoProject(PersistedFieldSnapshotMixin, UserCreatedBaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.KippoOrganization", on_delete=models.CASCADE, verbose_name=_("組織"))
    name = models.CharField(max_length=256, unique=True, verbose_name=_("プロジェクト名"), help_text=_("プロジェクトの名称"))
    slug = models.CharField(max_length=300, unique=True, editable=False)
    phase = models.CharField(
        max_length=150,
        default=DEFAULT_PROJECT_PHASE,
        choices=VALID_PROJECT_PHASES,
        verbose_name=_("フェーズ"),
        help_text=_("プロジェクトの状態（フェーズ）"),
    )
    confidence = models.PositiveSmallIntegerField(
        default=PHASE_CONFIDENCE[DEFAULT_PROJECT_PHASE],
        editable=False,
        validators=(MaxValueValidator(100), MinValueValidator(0)),
        verbose_name=_("確度"),
        help_text=_("0〜100。フェーズから自動算出されます（読み取り専用）"),
    )
    category = models.ForeignKey(
        "projects.KippoProjectOrganizationCategory",
        on_delete=models.PROTECT,
        related_name="projects",
        default=get_default_project_category,
        verbose_name=_("カテゴリ"),
    )
    lead_source = models.CharField(
        max_length=LEAD_SOURCE_MAX_LENGTH,
        blank=True,
        default="",
        choices=VALID_LEAD_SOURCES,
        verbose_name=_("リード"),
        help_text=_("案件のリード獲得元（任意）"),
    )
    slack_channel_name = models.CharField(
        max_length=80,
        blank=True,
        default="",
        verbose_name=_("Slack会話チャンネル名"),
        help_text=_("会話用チャンネル。組織のSlackボットを招待するとチャンネル通知が有効になります"),
    )
    slack_notification_channel_name = models.CharField(
        max_length=80,
        blank=True,
        default="",
        verbose_name=_("Slack通知チャンネル名"),
        help_text=_("クローラー・バッチ・開発通知用のチャンネル（会話用チャンネルとは別）"),
    )
    enable_cost_report = models.BooleanField(
        default=False,
        verbose_name=_("コストレポート有効化"),
        help_text=_("設定済みのSlackチャンネルへコストレポートを送信する場合はTrueに設定します"),
    )
    columnset = models.ForeignKey(
        ProjectColumnSet,
        on_delete=models.DO_NOTHING,
        verbose_name=_("カラムセット"),
        help_text=_("Kippoを通じて関連するGitHubプロジェクトを作成する際に使用するProjectColumnSet"),
    )
    project_manager = models.ForeignKey(
        "accounts.KippoUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("プロジェクトマネージャー"),
        help_text=_("プロジェクトに割り当てるプロジェクトマネージャー"),
    )
    parent_project = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="continuation_children",
        verbose_name=_("親プロジェクト"),
        help_text=_("継続プロジェクトの元（親）プロジェクト"),
    )
    customer = models.ForeignKey(
        "customers.KippoCustomer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="projects",
        verbose_name=_("顧客"),
        help_text=_("このプロジェクトの納品先となる顧客（任意）"),
    )
    is_closed = models.BooleanField(_("プロジェクト終了済み"), default=False, help_text=_("プロジェクト完了時に手動で設定します"))
    close_comment = models.TextField(_("終了コメント"), blank=True, default="")
    display_as_active = models.BooleanField(
        _("アクティブとして表示"),
        default=True,
        help_text=_("Trueの場合、アクティブプロジェクト一覧に表示されます"),
    )
    display_in_project_report = models.BooleanField(
        _("プロジェクトレポートサマリ(Slack)に表示"),
        default=True,
        help_text=_("Trueの場合、プロジェクトレポートサマリに表示されます"),
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
        help_text=_("プロジェクト完了に必要な見積工数（人日）"),
    )
    start_date = models.DateField(_("開始日"), null=True, blank=True, help_text=_("プロジェクトがエンジニアリングリソースを必要とする日"))
    target_date = models.DateField(
        _("完了予定日"),
        null=True,
        blank=True,
        default=get_target_date_default,
        help_text=_("プロジェクトの完了予定日"),
    )
    actual_date = models.DateField(
        _("完了日"),
        null=True,
        blank=True,
        help_text=_("プロジェクトが実際に完了した日（当初の予定日ではありません）"),
    )
    document_folder_url = models.URLField(
        _("ドキュメント保管URL"),
        blank=True,
        default="",
        help_text=_("プロジェクトのドキュメントを保管しているURL"),
    )
    docbase_tag = CommaSeparatedCharField(
        _("DocBaseタグ"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("クローラーが該当する投稿を取得するために使用するカンマ区切りのDocBaseタグ（例: 'foo,bar'）"),
    )
    problem_definition = models.TextField(
        _("プロジェクト紹介"),
        blank=True,
        default="",
        help_text=_("このプロジェクトが解決しようとする課題を定義します"),
    )
    survey_issued = models.BooleanField(default=False, verbose_name=_("アンケート発行済み"), help_text=_("アンケートを発行したら更新してください"))
    survey_issued_datetime = models.DateTimeField(
        null=True,
        editable=False,
        verbose_name=_("アンケート発行日時"),
        help_text=_("「アンケート発行済み」フラグが設定されたときに更新されます"),
    )

    class Meta:
        verbose_name = _("プロジェクト")
        verbose_name_plural = verbose_name

    def clean(self):
        if self.actual_date and self.actual_date > timezone.now().date():
            raise ValidationError(_("指定された日付が未来の日付です"))
        if self.enable_cost_report and not self.slack_channel_name:
            raise ValidationError(_("コストレポートを有効にする場合はSlack会話チャンネル名が必須です"))
        # 契約(稼働中) requires the contract (with its period) to exist first: the project is created
        # (registration collects only the slim required set), the contract is added on a later edit,
        # and only then can the phase move to under-contract — at which point the contract period
        # drives the project dates. Gated on the TRANSITION into the phase only, so rows already
        # persisted in 契約(稼働中) (legacy projects predating contracts) stay editable/saveable.
        # The admin change form defers this gate to the contract inline (``_admin_defers_contract_gate``):
        # it is surfaced on the 契約 component and validated against the contract submitted in the SAME
        # request, so filling the inline and flipping the phase in one save works. Every other caller
        # (API model validation, direct full_clean, shell) still enforces it here.
        if self._is_entering_under_contract() and not getattr(self, "_admin_defers_contract_gate", False):
            contract = self.get_contract()
            if not (contract and contract.has_complete_period()):
                raise ValidationError({"phase": UNDER_CONTRACT_REQUIRES_CONTRACT_MSG})

    def _is_entering_under_contract(self) -> bool:
        """True only when this save MOVES the phase into 契約(稼働中) (including a create directly in
        that phase). A row already persisted in the phase is not re-gated, so legacy projects that
        reached 契約(稼働中) before contracts existed remain editable. Uses the persisted-phase
        snapshot taken in ``from_db`` (absent on an unsaved instance → treated as entering).
        """
        return self.phase == PHASE_UNDER_CONTRACT and getattr(self, "_confidence_synced_phase", None) != PHASE_UNDER_CONTRACT

    def revenue_entries(
        self,
        window_start: datetime.date | None = None,
        window_end: datetime.date | None = None,
    ) -> list[tuple[datetime.date, "Decimal"]]:
        """Revenue from the billing ledger as ``(billing_date, amount)`` tuples (kippo#31 / T12).

        The ledger is the single source of truth for project revenue regardless of contract
        billing type (delivery contracts record their single billing, monthly contracts their
        generated months), so there is no double counting. The optional window is clamped by
        month (an entry is included if its month overlaps [window_start, window_end]). Empty when
        the project has no contract (the ledger hangs off the contract).
        """
        contract = self.get_contract()
        if not contract:
            return []
        entries = contract.billing_entries.all()
        if window_start:
            entries = entries.filter(billing_date__gte=first_of_month(window_start))
        if window_end:
            entries = entries.filter(billing_date__lt=first_of_next_month(window_end))
        return [(entry.billing_date, entry.amount) for entry in entries]

    @property
    def total_revenue(self) -> Decimal:
        """トータル売上 (kippo#32 / T13): realized/accrued revenue — the billing-ledger total.

        Derived from the ledger (the single source of truth established in kippo#31), so it
        never drifts from the per-entry billing records.
        """
        return sum((amount for _, amount in self.revenue_entries()), Decimal(0))

    def get_contract(self) -> "KippoProjectContract | None":
        """The project's single contract (kippo#31), or None when none has been created.

        The contract is OneToOne — one contract per project; a separate engagement is modelled as
        a new project, not a second contract. This is the safe accessor for the optional reverse
        relation (the reverse OneToOne raises when unset).
        """
        return getattr(self, "contract", None)

    @property
    def contract_amount(self) -> Decimal:
        """契約金額 (kippo#32 / T13): the project's contract total (0 when no contract, or when an
        effort contract has no fixed total/cap set).
        """
        contract = self.get_contract()
        return (contract.total_amount or Decimal(0)) if contract else Decimal(0)

    @property
    def billing_types(self) -> list[str]:
        """請求方法 (kippo#39 / T14): the project's billing type, as a one-element list (empty
        when no contract). Kept as a list for API back-compat with the pre-OneToOne shape.
        """
        contract = self.get_contract()
        return [contract.billing_type] if contract else []

    @property
    def monthly_billing_schedule(self) -> list[tuple[datetime.date, Decimal]]:
        """Planned per-month billing schedule for a monthly contract (kippo#39 / T15).

        Sorted ``(month_end_date, amount)`` entries — lets the UI render one row per month for
        monthly-billing projects (月額は契約期間内毎月表示). Empty for delivery / no contract.
        """
        contract = self.get_contract()
        return contract.monthly_schedule() if contract else []

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
    def estimated_allocated_effort_hours(self) -> int | None:
        """予算工数 implied by a 納品/固定 contract, for a project with no allocated_staff_days entered.

        A delivery + fixed (納品/固定) contract bills a fixed 契約金額 for a fixed scope, so the amount
        implies a budget: 契約金額 ÷ 人日単価 × day_workhours. The rate is the project's developer
        ProjectAssignmentRate, falling back to settings.DEFAULT_PROJECT_DAILY_RATE (the same resolution
        used for requirement estimates). `assignment_rates` is walked in python so a prefetched list
        context costs no extra query.

        None for any other contract shape — 月額 bills per month and 実績 bills actuals, so neither
        amount states a scope — and for a contract with no 契約金額 or an org with no day_workhours.
        """
        contract = self.get_contract()
        if not contract or contract.billing_type != BILLING_TYPE_DELIVERY or contract.pricing_basis != PRICING_BASIS_FIXED:
            return None
        if not contract.total_amount or not self.organization.day_workhours:
            return None
        rate_per_day = next(
            (rate.rate_per_day for rate in self.assignment_rates.all() if rate.role == ProjectRoles.DEVELOPER.value),
            settings.DEFAULT_PROJECT_DAILY_RATE,
        )
        if not rate_per_day:
            return None
        return round((contract.total_amount / Decimal(rate_per_day)) * Decimal(self.organization.day_workhours))

    @property
    def is_estimated_allocated_effort_hours(self) -> bool:
        """True when `allocated_effort_hours` is the 納品/固定 contract estimate, not an entered value."""
        if self.allocated_staff_days and self.organization.day_workhours:
            return False
        return self.estimated_allocated_effort_hours is not None

    @property
    def allocated_effort_hours(self) -> int | None:
        """予算工数: the entered 割当工数(人日) × day_workhours.

        Falls back to the 納品/固定 contract estimate while no allocated_staff_days is entered, so a
        fixed-price project still carries a budget (and therefore a progress status) before it is
        estimated. The entered value always wins once it is defined.
        """
        if self.allocated_staff_days and self.organization.day_workhours:
            return self.allocated_staff_days * self.organization.day_workhours
        estimated_effort_hours = self.estimated_allocated_effort_hours
        if estimated_effort_hours is not None:
            return estimated_effort_hours
        logger.warning(
            f"Project.allocated_staff_days and/or Project.organization.day_workhours not set: project={self}, organization={self.organization}"
        )
        return None

    def get_projecteffort_values(self, total_effort: object = _COMPUTE) -> tuple[int, int | None, float | None]:
        actual_effort_hours = self.get_total_effort() if total_effort is _COMPUTE else total_effort
        total_effort_percentage = None
        if actual_effort_hours and self.allocated_effort_hours:
            total_effort_percentage = (actual_effort_hours / self.allocated_effort_hours) * 100
        return actual_effort_hours, self.allocated_effort_hours, total_effort_percentage

    def get_expected_effort(self, at_date: datetime.date | None = None, holidays: object = _COMPUTE) -> tuple[int | None, float | None]:
        """Expected effort (days, hours) for the project at `at_date` (default: today). Days is always None.

        `holidays` may be a precomputed collection of holiday `datetime.date`s (membership is
        all that is tested) to avoid a per-project PublicHoliday query in bulk/list contexts.
        See `projects.services.progress.expected_effort_hours` for the rule.
        """
        at_date = at_date or timezone.localdate()
        expected_hours = expected_effort_hours(self, [at_date], holidays=None if holidays is _COMPUTE else holidays)
        return None, expected_hours[at_date]

    def get_projectprogressstatus_values(self, total_effort: object = _COMPUTE, holidays: object = _COMPUTE) -> ProjectProgressStatus:
        actual_effort_hours, allocated_effort_hours, total_effort_percentage = self.get_projecteffort_values(total_effort=total_effort)
        expected_effort_days, expected_effort_hours = self.get_expected_effort(holidays=holidays)
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
            is_estimated_allocated_effort_hours=self.is_estimated_allocated_effort_hours,
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
        today = timezone.localdate()
        return self.kippomilestone_set.filter(target_date__gte=today).order_by("-target_date")

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
        total_effort_hours = self.projectweeklyeffort_project.aggregate(Sum("hours"))
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

    # Persisted-value snapshots read by save(): phase gates the confidence re-derivation, and both
    # feed the フェーズ / 確度 change log (kippo#77). Field name -> snapshot attribute.
    PERSISTED_SNAPSHOT_ATTRIBUTES = {"phase": "_confidence_synced_phase", "confidence": "_persisted_confidence"}

    def _stamp_status_datetimes(self) -> None:
        if self.survey_issued and not self.survey_issued_datetime:
            self.survey_issued_datetime = timezone.now()

        if self.is_closed and not self.closed_datetime:
            self.closed_datetime = timezone.now()
        elif not self.is_closed and self.closed_datetime:
            self.closed_datetime = None

    def _sync_confidence_with_phase(self, save_kwargs: dict) -> None:
        """Re-derive confidence (確度) from phase when phase changes or on create. May add to update_fields.

        An existing or manually-set confidence is otherwise preserved (kippo#36 / T09). The
        migration that introduced phase set the initial values; ordinary edits leave them alone.
        A partial save (update_fields) that does not write phase must NOT touch confidence either,
        or the DB would hold a new confidence against the old phase.
        """
        update_fields = save_kwargs.get("update_fields")
        phase_persisted = update_fields is None or "phase" in update_fields
        if self._state.adding or (phase_persisted and self.phase != getattr(self, "_confidence_synced_phase", None)):
            self.confidence = PHASE_CONFIDENCE.get(self.phase, self.confidence)
            if update_fields is not None and "confidence" not in update_fields:
                save_kwargs["update_fields"] = [*update_fields, "confidence"]

    def _use_organization_category(self) -> None:
        """Point a new project at its organization's copy of the category.

        copy-on-create (kippo#49): a project references its ORG's category copy, never a global
        template row. The category resolves to a global only when the caller made no explicit
        choice (the field default), so prefer the org's configured default (kippo#50), then the
        org's copy of the same key, then the org's 'other'.
        """
        if not (self.category_id and self.organization_id and self.category.organization_id is None):
            return
        org_categories = KippoProjectOrganizationCategory.objects.filter(organization_id=self.organization_id)
        org_category = (
            org_categories.filter(is_default=True, is_active=True).first()
            or org_categories.filter(key=self.category.key).first()
            or org_categories.filter(key=DEFAULT_PROJECT_CATEGORY_VALUE).first()
        )
        if org_category:
            self.category = org_category

    def _phase_confidence_change(self, written_fields: Iterable[str] | None) -> dict | None:
        """The フェーズ / 確度 change this save makes to an existing project, or None (kippo#77).

        Only fields this save actually writes count: a field the save does not write keeps its
        snapshot. A create is not logged (created_by / created_datetime record the initial state).
        """
        previous_phase = getattr(self, "_confidence_synced_phase", None)
        previous_confidence = getattr(self, "_persisted_confidence", None)
        if self._state.adding or previous_phase is None or previous_confidence is None:
            return None
        saved_phase = self.phase if written_fields is None or "phase" in written_fields else previous_phase
        saved_confidence = self.confidence if written_fields is None or "confidence" in written_fields else previous_confidence
        if saved_phase == previous_phase and saved_confidence == previous_confidence:
            return None
        return {
            "previous_phase": previous_phase,
            "new_phase": saved_phase,
            "previous_confidence": previous_confidence,
            "new_confidence": saved_confidence,
        }

    def save(self, *args, **kwargs):
        self._stamp_status_datetimes()
        self._sync_confidence_with_phase(kwargs)
        if self._state.adding:
            self.slug = slugify(self.name, allow_unicode=True)
            self._use_organization_category()

        # Slack's slash-command payload sends "channel_name" without a leading "#",
        # so the slash-command project lookup ("...subcommands/projectstatus.py") does
        # an exact string match on the bare name. Store the canonical bare form here
        # so users entering "#proj-foo" or "  proj-foo  " in admin still resolve.
        self.slack_channel_name = _normalize_slack_channel_name(self.slack_channel_name)
        self.slack_notification_channel_name = _normalize_slack_channel_name(self.slack_notification_channel_name)

        written_fields = kwargs.get("update_fields")
        change = self._phase_confidence_change(written_fields)
        with transaction.atomic():
            super().save(*args, **kwargs)
            if change:
                KippoProjectPhaseConfidenceChange.objects.create(project=self, changed_by_id=self.updated_by_id, **change)
        # Refresh only the snapshots of fields this save wrote, so a partial save that skipped
        # phase doesn't poison the comparison for the next full save.
        self._snapshot_persisted_state(written_fields)

    @property
    def display_label(self) -> str:
        """Project name prefixed with the customer name when a customer is set."""
        return f"{self.customer.name} {self.name}" if self.customer else self.name

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.display_label})"


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


class SalesKippoProjectManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        # Open projects still in the pre-contract sales pipeline (proposing/verbal, KIT).
        qs = qs.filter(is_closed=False, phase__in=SALES_PROJECT_PHASES)
        return qs


class SalesKippoProject(KippoProject):
    objects = SalesKippoProjectManager()

    class Meta:
        proxy = True
        verbose_name = _("プロジェクト(営業中)")
        verbose_name_plural = verbose_name


class KippoProjectPhaseConfidenceChange(models.Model):
    """One フェーズ / 確度 change on a KippoProject (kippo#77). Written by KippoProject.save(); read-only.

    ``changed_by`` is the project's ``updated_by`` at save time. Every admin / API write path sets it;
    a script that saves without setting it attributes the change to the previous editor.
    """

    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, related_name="phase_confidence_changes", verbose_name=_("プロジェクト"))
    previous_phase = models.CharField(max_length=150, choices=VALID_PROJECT_PHASES, verbose_name=_("変更前フェーズ"))
    new_phase = models.CharField(max_length=150, choices=VALID_PROJECT_PHASES, verbose_name=_("変更後フェーズ"))
    previous_confidence = models.PositiveSmallIntegerField(verbose_name=_("変更前確度"))
    new_confidence = models.PositiveSmallIntegerField(verbose_name=_("変更後確度"))
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("変更者")
    )
    changed_datetime = models.DateTimeField(auto_now_add=True, verbose_name=_("変更日時"))

    class Meta:
        ordering = ("-changed_datetime",)
        verbose_name = _("フェーズ・確度変更履歴")
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"PhaseConfidenceChange({self.project_id} {self.previous_phase}->{self.new_phase} {self.previous_confidence}->{self.new_confidence})"


class KippoProjectStatus(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE)
    comment = models.TextField(help_text=_("Current Status"))

    def __str__(self) -> str:
        return f"ProjectStatus({self.project.display_label} {self.created_datetime})"
