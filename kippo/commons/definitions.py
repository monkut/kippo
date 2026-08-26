from enum import Enum

# define constants for weekend days
SATURDAY = 5
SUNDAY = 6
MONDAY = 0

# PersonalHoliday querysets filter on the holiday's START date (`day`), so a span that
# begins before the window and runs into it is not returned by a naive `day__gte=<window
# start>` filter. Reach this far back to catch those spans; the covered dates are then
# clipped to the window. Matches the lookback already used by
# accounts.viewsets._calc_available_workdays_in_month.
PERSONAL_HOLIDAY_LOOKBACK_DAYS = 365


class StringEnumWithChoices(str, Enum):
    @classmethod
    def choices(cls) -> tuple[tuple[str, str], ...]:
        return tuple((str(e.value), str(e.value)) for e in cls)

    @classmethod
    def values(cls) -> tuple:
        return tuple(e.value for e in cls)


class SlackResponseTypes(StringEnumWithChoices):
    """Enum for Slack response types."""

    IN_CHANNEL = "in_channel"
    EPHEMERAL = "ephemeral"


SLACK_REQUEST_EXPECTED_FIELDS = (
    "command",
    "text",
    "response_url",
    "user_id",
)
