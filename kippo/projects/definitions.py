import dataclasses
import datetime
import uuid
from typing import TYPE_CHECKING

from commons.definitions import StringEnumWithChoices
from django.utils.translation import gettext_lazy as _
from pydantic import BaseModel

if TYPE_CHECKING:
    from accounts.models import OrganizationMembership

    from .models import KippoProject


# Minimum effort percentage required for a user to be included in survey tracking
SURVEY_EFFORT_THRESHOLD_PERCENTAGE = 3

# A project's confidence (確度) must be at this percentage before its monthly
# assignments can be confirmed (is_confirmed=True).
FULL_CONFIDENCE_PERCENTAGE = 100

# The weekly-effort close offset (days after the month's last entry-start date, i.e. the Monday
# following its last Monday) is configurable per organization:
# KippoOrganization.weekly_effort_close_offset_days (min 7 — users get at least 1 week to enter).

# Shown by both the REST API and Django admin when a closed week is edited without an unlock.
WEEKLY_EFFORT_CLOSED_MESSAGE = _("締め日時を過ぎているため編集できません。Adminによるアンロックが必要です。")

# Default (global, organization=null) project categories seeded as KippoProjectOrganizationCategory rows.
# (key, label, sort_order). KippoProject.category is a FK to KippoProjectOrganizationCategory; these are the
# org-agnostic defaults an organization inherits until it defines its own categories (kippo#30 / T08, T20).
# The upsell-* values are retained here — the close→follow-up workflow still depends on them; they are
# dropped together with that workflow's rework in kippo#27 / #35.
DEFAULT_KIPPOPROJECT_CATEGORIES = (
    ("ai-development", _("AI開発"), 10),
    ("mathematical-optimization", _("数理最適化"), 20),
    ("si", "SI", 30),
    ("consulting", _("コンサルティング"), 40),
    ("advisory", _("アドバイザリー"), 50),
    ("other", _("その他"), 60),
    ("non-project", _("非案件"), 70),
    ("upsell-improvement", _("(Upsell) 追加改善・拡張"), 80),
    ("upsell-new-proposal", _("(Upsell) 新規提案"), 90),
    ("upsell-new-department", _("(Upsell) 別部署紹介"), 100),
)
KIPPOPROJECT_CATEGORY_CHOICES = tuple((key, label) for key, label, _sort in DEFAULT_KIPPOPROJECT_CATEGORIES)
# Identifies "non-project" KippoProjects (replaces the retired phase=="anon-project"; consumed by kippo#37 / T10).
NON_PROJECT_CATEGORY_VALUE = "non-project"
# Fallback category for KippoProjects whose pre-migration category had no equivalent in the new taxonomy.
DEFAULT_PROJECT_CATEGORY_VALUE = "other"
UPSELL_CATEGORY_VALUES = ("upsell-improvement", "upsell-new-proposal", "upsell-new-department")
KIPPOPROJECT_CATEGORY_MAX_LENGTH = 32
VALID_KIPPOPROJECT_CATEGORY_VALUES = tuple(choice[0] for choice in KIPPOPROJECT_CATEGORY_CHOICES)

# KippoProjectContract billing type (請求方法) — kippo#31 / T11,T12.
# "delivery" (納品): the contract amount is billed once at the contract's billing_date.
# "monthly" (月額): the contract amount accrues every month within the contract period.
BILLING_TYPE_DELIVERY = "delivery"
BILLING_TYPE_MONTHLY = "monthly"
VALID_BILLING_TYPES = (
    (BILLING_TYPE_DELIVERY, _("納品")),
    (BILLING_TYPE_MONTHLY, _("月額")),
)
DEFAULT_BILLING_TYPE = BILLING_TYPE_DELIVERY

# KippoProjectContract pricing basis (料金体系) — orthogonal to billing_type. billing_type sets
# *when* revenue is billed; pricing_basis sets *how* each entry's amount is computed.
# "fixed" (固定): bill the contract total_amount. "effort" (実績): bill actual logged effort
# (Σ hours ÷ day_workhours × role rate_per_day) — time-&-materials / 準委任 履行割合型.
PRICING_BASIS_FIXED = "fixed"
PRICING_BASIS_EFFORT = "effort"
VALID_PRICING_BASES = (
    (PRICING_BASIS_FIXED, _("固定")),
    (PRICING_BASIS_EFFORT, _("実績")),
)
DEFAULT_PRICING_BASIS = PRICING_BASIS_FIXED


class ProjectRoles(StringEnumWithChoices):
    """Roles for project assignment rates."""

    DEVELOPER = "developer"
    PROJECT_MANAGER = "project_manager"
    TESTER = "tester"


@dataclasses.dataclass
class ProjectProgressStatus:
    project: "KippoProject"
    date: datetime.date  # Date of the status
    current_effort_hours: int
    expected_effort_hours: int
    expected_effort_days: int
    allocated_effort_hours: int
    allocated_effort_days: int | None = None

    def get_difference_percentage(self) -> float | None:
        """Calculate the difference percentage between current and expected effort hours."""
        difference_percentage = None
        if self.current_effort_hours and self.expected_effort_hours:
            difference_percentage = ((self.current_effort_hours - self.expected_effort_hours) / self.expected_effort_hours) * 100
        return difference_percentage

    def effort_percentage(self) -> float | None:
        """Calculate the effort percentage based on allocated and expected effort hours."""
        if self.expected_effort_hours == 0:
            return 0.0
        return (self.allocated_effort_hours / self.expected_effort_hours) * 100


@dataclasses.dataclass
class ProjectAssignmentForecastUserContext:
    """Pre-fetched per-user inputs consumed by the forecast day-walking loop.

    Fields are keyed by `KippoUser.id` (int).
    """

    by_user_month: dict[int, dict[datetime.date, int]]
    user_membership: dict[int, "OrganizationMembership"]
    user_holiday_country: dict[int, int | None]
    public_holidays_by_country: dict[int, set[datetime.date]]
    user_personal_holidays: dict[int, set[datetime.date]]


class ProjectAssignmentPatternMember(BaseModel):
    """One member of a suggested assignment pattern.

    `monthly_percentages` keys are first-of-month dates; pydantic serializes them as
    ISO strings ("YYYY-MM-DD") via `model_dump(mode='json')`.
    """

    user_id: uuid.UUID
    is_past_member: bool
    monthly_percentages: dict[datetime.date, int]


class ProjectAssignmentPatternConflict(BaseModel):
    """An over-allocation point in a suggested project-assignment pattern."""

    user_id: uuid.UUID
    month: datetime.date
    reason: str


class ProjectAssignmentPattern(BaseModel):
    """A complete suggested project-assignment pattern.

    `pattern_ids` carries the strategy keys that produced this pattern. Normally a
    single id (e.g. ['P1-max-reuse']); when multiple strategies converge on the
    same member set + monthly percentages they are deduplicated into one
    ProjectAssignmentPattern with the union (e.g. ['P1-max-reuse', 'P2-blend'])
    — see kippo#227 S3.
    """

    pattern_ids: list[str]
    label: str
    estimated_completion: datetime.date | None
    infeasible: bool
    conflicts: list[ProjectAssignmentPatternConflict]
    members: list[ProjectAssignmentPatternMember]


class ValidCurrencies(StringEnumWithChoices):
    USD = "USD"
    JPY = "JPY"
    EUR = "EUR"


class ValidServices(StringEnumWithChoices):
    AWS = "AWS"
    AZURE = "AZURE"
    GCP = "GCP"
    OTHER = "OTHER"


class SkipReason(StringEnumWithChoices):
    """Structured reasons `auto_create_future_assignments` may skip persisting rows.

    Returned alongside `created_rows` as the second element of the service tuple so the
    REST endpoint, admin action, and signal log path can all distinguish "nothing to do"
    from "couldn't do it" without parsing log strings (kippo#19, #20).
    """

    PROJECT_CLOSED = "project_closed"
    MISSING_TARGET_DATE = "missing_target_date"
    MISSING_START_DATE = "missing_start_date"
    NO_SEED_SHAPE = "no_seed_shape"
    ALREADY_COMPLETE = "already_complete"
    FORECAST_UNAVAILABLE = "forecast_unavailable"
    NOT_CONFIRMED = "not_confirmed"
    NO_MISSING_MONTHS = "no_missing_months"
