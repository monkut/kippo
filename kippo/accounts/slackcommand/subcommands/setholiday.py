import logging

from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.conf import settings
from django.utils import timezone
from django.utils.text import gettext_lazy as _
from slack_sdk.web import SlackResponse, WebClient
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...models import OrganizationMembership, PersonalHoliday, SlackCommand

logger = logging.getLogger(__name__)


class SetHolidaySubCommand(SubCommandBase):
    """Command to clock out a user."""

    DISPLAY_COMMAND_NAME: str = "set-holiday"
    DESCRIPTION: str = _("退勤情報を登録。例) `COMMAND set-holiday YY/MM/DD`")
    ALIASES: set = {"AM半休", "午前休", "PM半休", "午後休", "半休", "休日", "set-holiday", "setholiday"}
    HALF_DAY_SUBCOMMANDS: set = {
        "AM半休",
        "午前休",
        "PM半休",
        "午後休",
        "半休",
    }

    @classmethod
    def _prepare_valid_response_blocks(
        cls, command: SlackCommand, entry_datetime: timezone.datetime, attendance_report_channel: str
    ) -> tuple[list[dict], SlackResponse | None]:
        """Prepare response blocks for a valid set-holiday."""
        text_without_subcommand = cls._get_text_without_subcommand(command)
        is_half_day = command.sub_command in cls.HALF_DAY_SUBCOMMANDS
        logger.debug(f"sub_command={command.sub_command}, is_half_day={is_half_day}")
        new_personalholiday = PersonalHoliday(
            user=command.user,
            is_half=is_half_day,
            duration=1,
            day=entry_datetime.date(),
        )
        new_personalholiday.save()

        # Prepare the response message
        half_day_text = "半休" if is_half_day else "全休"
        web_client = WebClient(token=command.organization.slack_api_token)
        user_image_url = None
        personalholiday_notification_blocks = []
        user_organization_membership = OrganizationMembership.objects.filter(user=command.user, organization=command.organization).first()
        # if user included a commend, include it in the message
        comment_display = f"\n> {text_without_subcommand}" if text_without_subcommand else ""
        if user_organization_membership:
            user_image_url = cls._get_user_image_url(web_client, user_organization_membership, refresh_days=settings.REFRESH_SLACK_IMAGE_URL_DAYS)
            if user_image_url:
                logger.debug(f"User {command.user.display_name} has slack_image_url: {user_image_url}")
                # Output message with user SLACK image

                personalholiday_notification_blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "image",
                                "image_url": user_image_url,
                                "alt_text": command.user.display_name,
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*{command.user.display_name}* は、`{entry_datetime.date()}の{half_day_text}` をとる予定{comment_display}",
                            },
                        ],
                    }
                )

        if not personalholiday_notification_blocks:
            logger.warning(f"User {command.user.display_name} has no slack_image_url: {user_image_url}")
            # Output message WITHOUT user SLACK image, fallback to :white_square:
            personalholiday_notification_blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{command.user.display_name}* は、`{entry_datetime.date()}の{half_day_text}` をとる予定{comment_display}",
                    },
                }
            )

        web_send_response = web_client.chat_postMessage(channel=attendance_report_channel, blocks=personalholiday_notification_blocks)

        command_response_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"`{attendance_report_channel}` チャンネルに通知をしました。",
                },
            }
        ]
        command.is_valid = True
        command.save()
        return command_response_blocks, web_send_response

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the check-in command."""
        web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)
        attendance_report_channel = command.organization.slack_attendance_report_channel

        # this is extra text provided by the user
        text_without_subcommand = cls._get_text_without_subcommand(command)
        organization_command_name = command.organization.slack_command_name

        # check if datetime is given in 'text'
        logger.debug(f"text_without_subcommand={text_without_subcommand}")

        # apply HH:MM if not given to enable parsing of date(time)
        if ":" not in text_without_subcommand:
            text_without_subcommand = f"{text_without_subcommand} 00:00"
            logger.debug(f"updated text_without_subcommand to include 00:00 for parsing: {text_without_subcommand}")

        entry_datetime = cls._get_datetime_from_text(text_without_subcommand)
        if not entry_datetime:
            logger.error(f"`entry_datetime` not parsed from text (expected YYYY/MM/DD): {text_without_subcommand}")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"休みの登録ができません。\n`/{organization_command_name} setholiday YY/MM/DD`の形式で登録してください。\n",
                    },
                }
            ]
        else:
            # check for existing PersonalHoliday entries
            search_start_datetime = entry_datetime - timezone.timedelta(days=30)
            existing_personalholiday_entries = PersonalHoliday.objects.filter(
                user=command.user,
                day__gte=search_start_datetime.date(),
                day__lte=entry_datetime.date(),
            )
            existing_personalholiday_dates = []
            for entry in existing_personalholiday_entries:
                existing_personalholiday_dates.append(entry.day)
                # PersonalHoliday stored as date + duration
                # -- build dates from duration
                for i in range(1, entry.duration + 1):
                    existing_personalholiday_dates.append(entry.day + timezone.timedelta(days=i))
            logger.debug(f"existing_personalholiday_dates={existing_personalholiday_dates}")

            if entry_datetime.date() in existing_personalholiday_dates:
                logger.error(
                    f"`PersonalHoliday` already exists for date: "
                    f"user={command.user.username}, "
                    f"date={entry_datetime.date()}, "
                    f"text_without_subcommand={text_without_subcommand}"
                )
                command_response_blocks = [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"休みがすでに（{entry_datetime.date()}）に登録されています。"},
                    }
                ]
            else:
                logger.debug(f"No PersonalHoliday entries found for {command.user.username} at {entry_datetime.date()} adding new entry ...")
                command_response_blocks, web_send_response = cls._prepare_valid_response_blocks(command, entry_datetime, attendance_report_channel)
                # logger.debug(f"No PersonalHoliday entries found for {command.user.username} at {entry_datetime.date()} adding new entry ...")
                #
                # is_half_day = command.sub_command in cls.HALF_DAY_SUBCOMMANDS
                # logger.debug(f"sub_command={command.sub_command}, is_half_day={is_half_day}")
                # new_personalholiday = PersonalHoliday(
                #     user=command.user,
                #     is_half=is_half_day,
                #     duration=1,
                #     day=entry_datetime.date(),
                # )
                # new_personalholiday.save()
                #
                # # Prepare the response message
                # half_day_text = "半休" if is_half_day else "全休"
                # personalholiday_notification_blocks = [
                #     {
                #         "type": "section",
                #         "text": {
                #             "type": "mrkdwn",
                #             "text": f"*{command.user.display_name}* は、`{entry_datetime.date()}の{half_day_text}`をとる予定\n",
                #         },
                #     }
                # ]
                #
                # web_client = WebClient(token=command.organization.slack_api_token)
                # web_send_response = web_client.chat_postMessage(channel=attendance_report_channel, blocks=personalholiday_notification_blocks)
                #
                # command_response_blocks = [
                #     {
                #         "type": "section",
                #         "text": {
                #             "type": "mrkdwn",
                #             "text": f"`{attendance_report_channel}`チャンネルに通知をしました。",
                #         },
                #     }
                # ]
                # command.is_valid = True
                # command.save()

        # Notify user that notification was sent to the registered channel
        webhook_client = WebhookClient(command.response_url)
        webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)
        return command_response_blocks, web_send_response, webhook_send_response
