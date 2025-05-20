from enum import Enum

# define constants for weekend days
SATURDAY = 5
SUNDAY = 6
MONDAY = 0


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
