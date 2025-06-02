import dataclasses
import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import KippoProject


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
