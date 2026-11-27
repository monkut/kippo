import datetime
import logging
from operator import attrgetter

from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.utils import timezone
from django.utils.text import gettext_lazy as _
from slack_sdk.web import SlackResponse, WebClient
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...definitions import AttendanceRecordCategory
from ...handlers.functions import _prepare_organization_user_status_blocks
from ...models import AttendanceRecord, OrganizationMembership, SlackCommand

logger = logging.getLogger(__name__)

# a local day lasts 25 hours when clocks go back
LONGEST_LOCAL_DAY_HOURS = 25


class ListUsersSubCommand(SubCommandBase):
    """Command to list status of organization users."""

    DISPLAY_COMMAND_NAME: str = "list-users"
    DESCRIPTION: str = _("ユーザの出勤状況を表示。例) `COMMAND list-users`")
    ALIASES: set = {
        "listusers",
        "list-users",
    }

    @staticmethod
    def _start_of_today(tzinfo: datetime.tzinfo) -> datetime.datetime:
        """Midnight at the start of today in `tzinfo`."""
        return datetime.datetime.combine(timezone.localdate(timezone=tzinfo), datetime.time.min, tzinfo=tzinfo)

    @classmethod
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the check-in command."""
        member_timezone = cls._member_timezone(command)
        web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)

        viewer_today = timezone.localdate(timezone=member_timezone)

        # The latest attendance record of each member, when it falls in that member's own today.
        # Members may work in different timezones, so the query takes the longest possible local
        # day and each record is then checked against its owner's midnight.
        recent_attendance_records = (
            AttendanceRecord.objects.filter(
                organization=command.organization,
                entry_datetime__gte=timezone.now() - datetime.timedelta(hours=LONGEST_LOCAL_DAY_HOURS),
            )
            .select_related("created_by__holiday_country")
            .order_by("created_by", "-entry_datetime")
            .distinct("created_by")
        )
        latest_user_attendance_records = [
            record
            for record in recent_attendance_records
            if record.entry_datetime >= cls._start_of_today(record.created_by.get_timezone(command.organization))
        ]

        if latest_user_attendance_records:
            category_display_mapping = {
                AttendanceRecordCategory.START: "出勤中",
                AttendanceRecordCategory.END: "退勤中",
                AttendanceRecordCategory.BREAK_START: "出勤中 (休憩中)",
                AttendanceRecordCategory.BREAK_END: "出勤中",
            }
            users = [record.created_by for record in latest_user_attendance_records]
            max_display_name_length = max(len(user.display_name) for user in users)  # for display padding
            organizationmembership_by_username = {
                m.user.username: m for m in OrganizationMembership.objects.filter(organization=command.organization, user__in=users)
            }
            user_status_blocks = []
            web_client = WebClient(token=command.organization.slack_api_token)
            for record in sorted(latest_user_attendance_records, key=attrgetter("entry_datetime")):
                user_organization_membership = organizationmembership_by_username.get(record.created_by.username, None)
                user_display_name = f"*{record.created_by.display_name}*"
                local_entry_datetime = record.entry_datetime.astimezone(member_timezone)
                local_entry_datetime_display = local_entry_datetime.strftime("%-m/%-d %-H:%M")
                category_display = category_display_mapping.get(record.category, record.category)
                message = f" {user_display_name:<{max_display_name_length}} {local_entry_datetime_display:>25} {category_display}"
                user_status_block = cls._prepare_message_block_with_user_image(
                    message=message, web_client=web_client, user_organization_membership=user_organization_membership
                )
                user_status_blocks.append(user_status_block)

            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"今日（{viewer_today}）の出勤記録:\n",
                    },
                }
            ]
            command_response_blocks.extend(user_status_blocks)

        else:
            logger.warning(f"No AttendenceRecord(s) found for organization {command.organization} on {viewer_today}")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"今日（{viewer_today}）出勤記録がみつかりません。",
                    },
                }
            ]

        # get personal holidays for the organization
        web_client = WebClient(token=command.organization.slack_api_token)  # needed to get user images
        user_status_blocks, _ = _prepare_organization_user_status_blocks(
            command.organization, web_client=web_client, include_no_personalholidays=True, today=viewer_today
        )
        command_response_blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"今日（{viewer_today}）休予定のメンバー:\n",
                },
            }
        )
        command_response_blocks.extend(user_status_blocks)

        # Notify user that notification was sent to the registered channel
        webhook_client = WebhookClient(command.response_url)
        webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)
        return command_response_blocks, web_send_response, webhook_send_response
