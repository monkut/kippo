import datetime
import logging
import re
from abc import abstractmethod
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils import timezone
from slack_sdk.web import SlackResponse, WebClient
from slack_sdk.webhook import WebhookResponse

if TYPE_CHECKING:
    from accounts.models import OrganizationMembership, SlackCommand


logger = logging.getLogger(__name__)


class SubCommandBase:
    """Base class for all command classes."""

    DISPLAY_COMMAND_NAME: str = "未定義"
    DESCRIPTION: str = "未定義"
    ALIASES: set = {}

    @classmethod
    def _is_valid_subcommand_alias(cls, alias: str) -> bool:
        """Check if the alias is valid."""
        if alias.strip() not in cls.ALIASES:
            raise ValueError(f"Invalid subcommand alias: {alias} not in {cls.ALIASES}")
        return True

    @classmethod
    def _get_text_without_subcommand(cls, command: "SlackCommand") -> str:
        """Get the text without the subcommand."""
        text_without_subcommand = command.text.split(command.sub_command, 1)[-1].strip()
        return text_without_subcommand

    @staticmethod
    def __add_year_to_text(text: str) -> str:
        """
        Add the current year to the text if it does not contain a full date.
        - MM/DD HH:MM -> YYYY/MM/DD HH:MM
        - MM-DD HH:MM -> YYYY-MM-DD HH:MM
        """
        if text.count(":"):
            if text.count("/") == 1:
                # add year to text_without_subcommand
                text = f"{timezone.localdate().year}/{text}"
            elif text.count("-") == 1:
                # add year to text_without_subcommand
                text = f"{timezone.localdate().year}-{text}"
        return text

    @classmethod
    def _get_datetime_from_text(cls, text: str, tzinfo: datetime.timezone = settings.JST) -> datetime.datetime | None:
        """Extract the first datetime from text"""
        text = cls.__add_year_to_text(text)
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
                    parsed_result = datetime.datetime.strptime(first_datetime, format_string).replace(tzinfo=tzinfo)
                except ValueError:
                    logger.warning(f"unable to parse datetime: {first_datetime}")
        else:
            logger.warning(f"unable to find datetime in text: '{text}'")

        return parsed_result

    @classmethod
    def _get_user_image_url(
        cls, web_client: WebClient, user_organization_membership: "OrganizationMembership", refresh_days: int = settings.REFRESH_SLACK_IMAGE_URL_DAYS
    ) -> str | None:
        """Get the user image URL from the Slack API."""
        min_update_datetime = timezone.now() - datetime.timedelta(days=refresh_days)  # period to update user image URL
        user_image_url = user_organization_membership.slack_image_url
        slack_user_id = user_organization_membership.slack_user_id
        logger.debug(f"updated_datetime={user_organization_membership.updated_datetime}")
        logger.debug(f"min_update_datetime={min_update_datetime}")

        if slack_user_id and (not user_image_url or (user_image_url and user_organization_membership.updated_datetime < min_update_datetime)):
            try:
                # get user icon image url
                result = web_client.users_info(user=slack_user_id)
                user_image_url = result["user"]["profile"].get("image_192", None)
                user_organization_membership.slack_image_url = user_image_url
                user_organization_membership.save()
                logger.info(
                    f"Updated OrganizationMembership.user_image_url for user {user_organization_membership.user.username} to {user_image_url}"
                )
            except Exception as e:
                logger.exception(f"Error processing attendance record for user {user_organization_membership.user}: {e.args}")
        return user_image_url

    @classmethod
    @abstractmethod
    def handle(cls, command: "SlackCommand") -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the specific command."""
        raise NotImplementedError("Subclasses must implement this method.")
