import dataclasses
import datetime
from typing import TYPE_CHECKING

from commons.definitions import StringEnumWithChoices
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from .models import KippoProject


# Minimum effort percentage required for a user to be included in survey tracking
SURVEY_EFFORT_THRESHOLD_PERCENTAGE = 3

KIPPOPROJECT_CATEGORY_CHOICES = (
    ("new-proposal", _("新規提案")),
    ("maintenance", _("保守")),
    ("poc", "poc"),
    ("instructor", _("講師")),
    ("r-and-d", "R&D"),
    ("PAO", "PAO"),
    ("upsell-improvement", _("(Upsell) 追加改善・拡張")),
    ("upsell-new-proposal", _("(Upsell) 新規提案")),
    ("upsell-new-department", _("(Upsell) 別部署紹介")),
    ("other", _("その他")),
)
UPSELL_CATEGORY_VALUES = ("upsell-improvement", "upsell-new-proposal", "upsell-new-department")
KIPPOPROJECT_CATEGORY_MAX_LENGTH = 32
VALID_KIPPOPROJECT_CATEGORY_VALUES = tuple(choice[0] for choice in KIPPOPROJECT_CATEGORY_CHOICES)


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


class ValidCurrencies(StringEnumWithChoices):
    USD = "USD"
    JPY = "JPY"
    EUR = "EUR"


class ValidServices(StringEnumWithChoices):
    AWS = "AWS"
    AZURE = "AZURE"
    GCP = "GCP"
    OTHER = "OTHER"
