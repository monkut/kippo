import datetime
import logging
import re
from abc import abstractmethod
from typing import TYPE_CHECKING

from django.conf import settings
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookResponse

if TYPE_CHECKING:
    from accounts.models import SlackCommand


logger = logging.getLogger(__name__)


class SubCommandBase:
    """Base class for all command classes."""

    DISPLAY_COMMAND_NAME: str = "未定義"
    DESCRIPTION: str = "未定義"
    ALIASES: set = {}

    @classmethod
    def _is_valid_subcommand_alias(cls, alias: str) -> bool:
        """Check if the alias is valid."""
        if alias not in cls.ALIASES:
            raise ValueError(f"Invalid subcommand alias: {alias} not in {cls.ALIASES}")
        return True

    @classmethod
    def _get_text_without_subcommand(cls, command: "SlackCommand") -> str:
        """Get the text without the subcommand."""
        text_without_subcommand = command.text.split(command.sub_command, 1)[-1].strip()
        return text_without_subcommand

    @classmethod
    def _get_datetime_from_text(cls, text: str, timezone: datetime.timezone = settings.JST) -> datetime.datetime | None:
        """Extract the first datetime from text"""
        match_str = r"^(\d{2,4}(\/|-)\d{1,2}(\/|-)\d{1,2}\s\d{1,2}:\d{2})"
        m = re.match(match_str, text)

        parsed_result = None
        if m:
            format_string = None
            first_datetime = m.group(0)

            full_year_length = 4
            if first_datetime.count("/"):
                if len(first_datetime.split("/", 1)[0]) == full_year_length:
                    format_string = "%Y/%m/%d %H:%M"
                else:
                    format_string = "%y/%m/%d %H:%M"
            elif first_datetime.count("-"):
                if len(first_datetime.split("-", 1)[0]) == full_year_length:
                    format_string = "%Y-%m-%d %H:%M"
                else:
                    format_string = "%y-%m-%d %H:%M"

            if format_string:
                try:
                    # extract and apply timezone
                    parsed_result = datetime.datetime.strptime(first_datetime, format_string).replace(tzinfo=timezone)
                except ValueError:
                    logger.warning(f"unable to parse datetime: {first_datetime}")
        else:
            logger.warning(f"unable to find datetime in text: {text}")

        return parsed_result

    @classmethod
    @abstractmethod
    def handle(cls, command: "SlackCommand") -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the specific command."""
        raise NotImplementedError("Subclasses must implement this method.")
