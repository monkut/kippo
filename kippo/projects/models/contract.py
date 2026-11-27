"""Project contract and billing ledger."""

import datetime
from decimal import Decimal

from accounts.models import KippoUser
from commons.functions import first_of_month, first_of_next_month, last_of_month
from commons.models import UserCreatedBaseModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..definitions import (
    BILLING_TYPE_MONTHLY,
    DEFAULT_BILLING_TYPE,
    DEFAULT_PRICING_BASIS,
    PRICING_BASIS_EFFORT,
    PRICING_BASIS_FIXED,
    VALID_BILLING_TYPES,
    VALID_PRICING_BASES,
    ProjectRoles,
)
from .assignment import ProjectMonthlyAssignment
from .effort import ProjectWeeklyEffort
from .project import KippoProject


class KippoProjectContract(UserCreatedBaseModel):
    """The agreed billing terms for a project (kippo#31 / T11) — *how* the project is billed.

    One contract per project (OneToOne): a separate engagement is modelled as a new project, not
    a second contract. Terms live here rather than on ``KippoProject`` so a delivery project never
    carries a meaningless monthly field. Billing *events* live in the ``KippoProjectBillingEntry``
    ledger, generated from these terms via ``generate_billing_entries()``.
    """

    project = models.OneToOneField(KippoProject, on_delete=models.CASCADE, related_name="contract")
    billed_to = models.ForeignKey(
        "customers.KippoCustomer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billed_contracts",
        verbose_name=_("請求先"),
        help_text=_("請求先の顧客。未入力の場合はプロジェクトの顧客。"),
    )
    billing_type = models.CharField(
        _("請求方法"),
        max_length=20,
        choices=VALID_BILLING_TYPES,
        default=DEFAULT_BILLING_TYPE,
        help_text=_("請求するタイミング。納品: 契約終了日に一括。月額: 契約期間の各月末。"),
    )
    pricing_basis = models.CharField(
        _("料金体系"),
        max_length=20,
        choices=VALID_PRICING_BASES,
        default=DEFAULT_PRICING_BASIS,
        help_text=_("請求額の算出方法。固定: 契約金額。実績: 実績工数 × ロール単価。"),
    )
    total_amount = models.DecimalField(
        _("契約金額"),
        max_digits=12,
        decimal_places=0,
        null=True,
        blank=True,
        help_text=_("契約総額(円)。固定の場合は必須(月額請求では各月に均等配分し、端数は最終月)。実績の場合は任意(上限)。"),
    )
    estimated_monthly_amount = models.DecimalField(
        _("月額"),
        max_digits=12,
        decimal_places=0,
        null=True,
        blank=True,
        help_text=_(
            "実績 × 月額の契約のみ。各月をこの暫定額で請求し、入金前に「請求エントリを実績に修正」で実績額へ修正。"
            "未入力の場合は実績額をそのまま請求。"
        ),
    )
    start_date = models.DateField(
        _("契約開始日"),
        null=True,
        blank=True,
        help_text=_("未入力の場合はプロジェクトの開始日を自動設定。"),
    )
    end_date = models.DateField(
        _("契約終了日"),
        null=True,
        blank=True,
        help_text=_("未入力の場合はプロジェクトの完了予定日を自動設定。"),
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
        amount = f"¥{self.total_amount}" if self.total_amount is not None else "実績"
        return f"KippoProjectContract({self.project.display_label} {self.billing_type}/{self.pricing_basis} {amount})"

    @property
    def requires_estimate(self) -> bool:
        """True when the project needs an allocated_staff_days estimate — i.e. fixed-priced work.
        Effort (T&M) contracts are billed on actuals, so no upfront estimate is required.
        """
        return self.pricing_basis == PRICING_BASIS_FIXED

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError(_("Contract start_date is after end_date"))
        # total_amount is the contract total for fixed pricing (required); for effort it is an
        # optional cap and may be left blank.
        if self.pricing_basis == PRICING_BASIS_FIXED and self.total_amount is None:
            raise ValidationError({"total_amount": _("Total amount is required for fixed-price contracts.")})
        # the provisional amount only drives effort + monthly billing — anywhere else it would be
        # silently ignored, so reject it as a likely mistake
        if self.estimated_monthly_amount is not None and not (
            self.pricing_basis == PRICING_BASIS_EFFORT and self.billing_type == BILLING_TYPE_MONTHLY
        ):
            raise ValidationError({"estimated_monthly_amount": _("Estimated monthly amount (月額) only applies to effort + monthly contracts.")})

    def save(self, *args, **kwargs):
        is_initial_creation = self._state.adding
        # auto-populate the contract period from the project when left blank AT CREATION only. On a
        # later edit a blank date is honored (not re-filled), so end_date can be cleared to model an
        # open-ended / retainer engagement with no fixed completion (T&M).
        if is_initial_creation:
            if not self.start_date:
                self.start_date = self.project.start_date
            if not self.end_date:
                self.end_date = self.project.target_date
            # default the billed customer (請求先) to the project's assigned customer; a later edit can
            # point it at a different KippoCustomer when billing goes elsewhere.
            if self.billed_to_id is None:
                self.billed_to = self.project.customer
        super().save(*args, **kwargs)
        self._sync_project_period()
        # Populate the billing ledger from the terms on initial creation so the user does not have to
        # run the generate_billing_entries admin action by hand. Idempotent and safe to re-run later
        # (as months elapse or effort accrues) via the still-available action / API.
        if is_initial_creation:
            self.generate_billing_entries(created_by=self.created_by)

    def has_complete_period(self) -> bool:
        """True when both period endpoints are set — the contract can drive the project dates and
        satisfy the 契約(稼働中) phase gate. Shared by the model gate and the API serializer.
        """
        return bool(self.start_date and self.end_date)

    def _sync_project_period(self) -> None:
        """Once a contract exists its period is the single source of truth: mirror start_date/end_date
        onto the project's start_date/target_date so every consumer of the project columns
        (autoassign, forecast, changelist ordering, UI sorting) keeps working unchanged.
        Never clears a project date — a blank contract date leaves the project value as-is.
        """
        project = self.project
        update_fields = []
        if self.start_date and project.start_date != self.start_date:
            project.start_date = self.start_date
            update_fields.append("start_date")
        if self.end_date and project.target_date != self.end_date:
            project.target_date = self.end_date
            update_fields.append("target_date")
        if update_fields:
            # Attribute the date change to whoever edited the contract (set on the contract by the
            # viewset/admin), not the project's previous editor. Skip when the contract has no editor
            # so a good project.updated_by is never overwritten with NULL.
            if self.updated_by_id:
                project.updated_by = self.updated_by
                update_fields.append("updated_by")
            project.save(update_fields=[*update_fields, "updated_datetime"])

    def _contract_months(self) -> list[datetime.date]:
        """First-of-month dates for each calendar month the contract period [start_date, end_date]
        spans. Empty when the period is unresolved. The single source of the billed month set.
        """
        if not self.start_date or not self.end_date:
            return []
        months = []
        current = first_of_month(self.start_date)
        end = first_of_month(self.end_date)
        while current <= end:
            months.append(current)
            current = first_of_next_month(current)
        return months

    def _monthly_amounts(self, months: list[datetime.date]) -> list[Decimal]:
        """Per-month billed amounts for ``months``: ``total_amount`` split evenly across the contract
        months as whole yen, with the rounding remainder added to the final month so the entries sum
        exactly to ``total_amount``. Empty when ``months`` is empty.
        """
        if not months:
            return []
        base = self.total_amount // len(months)
        amounts = [base] * len(months)
        amounts[-1] += self.total_amount - base * len(months)
        return amounts

    def monthly_schedule(self) -> list[tuple[datetime.date, Decimal]]:
        """Planned per-month billing schedule for a monthly contract (kippo#39 / T15).

        One ``(month_end_date, amount)`` entry per calendar month in the contract period, matching
        the month-end (月末) dates and split amounts generate_billing_entries() bills. Empty for
        delivery contracts, effort pricing (amounts depend on not-yet-logged hours), and when the
        period is unresolved. This is the *planned* schedule derived from the contract terms (so it
        is available before any ledger entries are generated).
        """
        if self.billing_type != BILLING_TYPE_MONTHLY or self.pricing_basis != PRICING_BASIS_FIXED:
            return []
        months = self._contract_months()
        return [(last_of_month(month), amount) for month, amount in zip(months, self._monthly_amounts(months), strict=True)]

    def _effort_amount(self, period_start: datetime.date | None, period_end: datetime.date | None) -> Decimal:
        """Billed amount for effort logged within ``[period_start, period_end]``.

        Sum over the project's ``ProjectWeeklyEffort`` rows whose ``week_start`` falls in the period
        of ``hours ÷ day_workhours × rate_per_day``, rounded to whole yen. A weekly row is attributed
        to the month of its ``week_start`` (Monday) for role lookup. The billing-rate role comes from
        the user's ``ProjectMonthlyAssignment`` for that month (blank → developer); the rate comes
        from the project's ``ProjectAssignmentRate`` for that role (missing → the settings default).
        """
        if not period_start or not period_end:
            return Decimal(0)
        day_workhours = self.project.organization.day_workhours or 7
        rates = {rate.role: rate.rate_per_day for rate in self.project.assignment_rates.all()}
        default_rate = settings.DEFAULT_PROJECT_DAILY_RATE
        developer_role = ProjectRoles.DEVELOPER.value
        # (user_id, first-of-month) -> billing role, for the assignments that set one
        assignment_roles: dict[tuple, str] = {}
        for assignment in ProjectMonthlyAssignment.objects.filter(project=self.project).exclude(role=""):
            if assignment.month:
                assignment_roles[(assignment.user_id, first_of_month(assignment.month))] = assignment.role
        efforts = ProjectWeeklyEffort.objects.filter(
            project=self.project,
            week_start__gte=period_start,
            week_start__lte=period_end,
        )
        total = Decimal(0)
        for effort in efforts:
            role = assignment_roles.get((effort.user_id, first_of_month(effort.week_start))) or developer_role
            rate = rates.get(role, default_rate)
            total += (Decimal(effort.hours) / Decimal(day_workhours)) * Decimal(rate)
        return total.quantize(Decimal("1"))

    def effort_actual_amount(self, month: datetime.date) -> Decimal:
        """Actual (実績) billed amount for the calendar month containing ``month`` — the logged
        effort × role rate that a provisional (仮) entry is corrected to (kippo#46).
        """
        return self._effort_amount(first_of_month(month), last_of_month(month))

    def trueup_billing_entries(self, updated_by: KippoUser | None = None) -> int:
        """Correct provisional (仮) entries of an effort + monthly contract to actuals (実績) — kippo#46.

        Recomputes each unreceived entry's amount from the effort logged in the calendar month of its
        ``billing_date``. A zero-effort month sets the amount to 0 (the entry is kept, not deleted).
        Received (入金済) entries are final and left untouched. No-op for any other contract shape.
        Returns the number of entries updated.
        """
        if not (self.pricing_basis == PRICING_BASIS_EFFORT and self.billing_type == BILLING_TYPE_MONTHLY):
            return 0
        updated_count = 0
        for entry in self.billing_entries.filter(is_received=False):
            actual_amount = self.effort_actual_amount(entry.billing_date)
            if entry.amount != actual_amount:
                entry.amount = actual_amount
                if updated_by:
                    entry.updated_by = updated_by
                entry.save()
                updated_count += 1
        return updated_count

    def _billing_schedule(self) -> list[tuple[datetime.date, Decimal]]:
        """``(billing_date, amount)`` pairs to bill, per the billing_type × pricing_basis matrix.

        ``billing_type`` picks the dates (one per month at month-end, or one at ``end_date``);
        ``pricing_basis`` picks each amount (split ``total_amount`` for fixed, Σ effort for effort).
        The single source ``generate_billing_entries()`` bills from.
        """
        if self.pricing_basis == PRICING_BASIS_EFFORT:
            if self.billing_type == BILLING_TYPE_MONTHLY:
                # When the 月額 is set, bill it provisionally for every contract month (future months
                # included, no proration for partial months) so entries and planned revenue exist before
                # effort is logged; trueup_billing_entries() corrects to actuals later (kippo#46).
                # Otherwise bill each month's logged actuals directly.
                provisional = self.estimated_monthly_amount
                return [
                    (last_of_month(month), provisional if provisional is not None else self.effort_actual_amount(month))
                    for month in self._contract_months()
                ]
            # effort + delivery: settle the whole period's effort once at end_date
            if not (self.start_date and self.end_date):
                return []
            return [(self.end_date, self._effort_amount(self.start_date, self.end_date))]
        # fixed pricing
        if self.billing_type == BILLING_TYPE_MONTHLY:
            return self.monthly_schedule()
        # fixed + delivery
        if not self.end_date:
            return []
        return [(self.end_date, self.total_amount)]

    def planned_billing_schedule(self) -> list[tuple[datetime.date, Decimal]]:
        """Planned ``(billing_date, amount)`` entries across all billing types (kippo#31).

        Monthly contracts spread ``total_amount`` per month (月末 dates); delivery contracts produce a
        single entry at ``end_date``; effort amounts come from logged effort. The same schedule
        ``generate_billing_entries()`` bills from, exposed for planned-revenue reporting before any
        ledger entries exist.
        """
        return self._billing_schedule()

    def generate_billing_entries(self, created_by: KippoUser | None = None) -> list["KippoProjectBillingEntry"]:
        """Populate the project's billing ledger from these terms (kippo#31 / T12).

        Entry dates come from ``billing_type``, amounts from ``pricing_basis`` (see
        ``_billing_schedule``):
        - billing_type monthly: one entry per contract month, dated the last day of the month (月末).
        - billing_type delivery: one entry at the contract ``end_date`` (auto-populated from the
          project target_date).
        - pricing_basis fixed: ``total_amount`` (split evenly across the months for monthly, with the
          remainder on the final month — no proration; adjust an entry afterwards if needed).
        - pricing_basis effort: actual logged effort × role rate for the period.

        Idempotent: dates that already have an entry (including manually adjusted ones) are left
        untouched, and zero/None amounts (e.g. a month with no logged effort) create nothing. Returns
        only the newly created entries.
        """
        existing_dates = set(self.billing_entries.values_list("billing_date", flat=True))
        missing_entries = [
            self._build_entry(entry_date, amount, created_by)
            for entry_date, amount in self._billing_schedule()
            if amount and entry_date not in existing_dates
        ]
        return KippoProjectBillingEntry.objects.bulk_create(missing_entries)

    def _build_entry(self, billing_date: datetime.date, amount: Decimal, created_by: KippoUser | None) -> "KippoProjectBillingEntry":
        return KippoProjectBillingEntry(
            contract=self,
            billing_date=billing_date,
            amount=amount,
            created_by=created_by,
            updated_by=created_by,
        )


class KippoProjectBillingEntry(UserCreatedBaseModel):
    """A single billing/revenue entry in a contract's billing ledger (kippo#31 / T11, T12).

    Every entry belongs to a ``KippoProjectContract`` — the contract is the unit of account, and
    the ledger is the single source of truth for its revenue. Entries are generated from the
    contract terms (one for a delivery contract, one per month for a monthly contract) and can then
    be adjusted (price revision, proration) or added by hand (``is_manual=True``). The project is
    reached via ``contract.project`` (contract↔project is OneToOne).
    """

    contract = models.ForeignKey(
        KippoProjectContract,
        on_delete=models.CASCADE,
        related_name="billing_entries",
        help_text=_("Contract this billing entry belongs to."),
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
    is_manual = models.BooleanField(
        _("手動追加"),
        default=False,
        help_text=_("True for hand-added entries; False for entries generated from the contract terms."),
    )
    is_received = models.BooleanField(
        _("入金済"),
        default=False,
        help_text=_("True once payment for this billing entry has been received."),
    )
    received_datetime = models.DateTimeField(
        _("入金日時"),
        null=True,
        blank=True,
        help_text=_("When payment was received. Auto-set when is_received is enabled; cleared when disabled."),
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("入金確認者"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_received_by",
        help_text=_("User who verified/received the payment. Stamped from the acting admin when is_received is set; cleared when disabled."),
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
        constraints = (models.UniqueConstraint(fields=("contract", "billing_date"), name="unique_billingentry_contract_billing_date"),)

    def __str__(self) -> str:
        return f"KippoProjectBillingEntry({self.contract.project.display_label} {self.billing_date} ¥{self.amount})"

    def save(self, *args, **kwargs):
        # keep the receipt fields consistent (mirrors KippoProject is_closed/closed_datetime).
        # received_by is stamped by the admin (request.user); here we only clear it when un-received.
        if self.is_received and not self.received_datetime:
            self.received_datetime = timezone.now()
        elif not self.is_received:
            self.received_datetime = None
            self.received_by = None
        super().save(*args, **kwargs)
