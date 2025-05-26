import datetime
import logging

from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase
from django.conf import settings
from django.utils import timezone
from django.utils.text import gettext_lazy as _
from slack_sdk.web import SlackResponse, WebClient
from slack_sdk.webhook import WebhookClient, WebhookResponse

from ...models import AttendanceRecord, OrganizationMembership, SlackCommand

logger = logging.getLogger(__name__)


class ListUsersSubCommand(SubCommandBase):
    """Command to list status of organization users."""

    DISPLAY_COMMAND_NAME: str = "list-users"
    DESCRIPTION: str = _("ユーザの出勤状況を表示。例) `COMMAND list-users`")
    ALIASES: set = {
        "listusers",
        "list-users",
    }

    @classmethod
    def _get_user_image_url(
        cls, web_client: WebClient, user_organization_membership: OrganizationMembership, refresh_days: int = settings.REFRESH_SLACK_IMAGE_URL_DAYS
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
    def handle(cls, command: SlackCommand) -> tuple[list[dict], SlackResponse | None, WebhookResponse]:
        """Handle the check-in command."""
        web_send_response = None

        assert cls._is_valid_subcommand_alias(command.sub_command)

        # get latest attendance record for each user/organization for the today
        today_start_datetime = datetime.datetime.combine(timezone.localdate(), datetime.time.min).replace(tzinfo=settings.JST)
        latest_user_attendance_records = list(
            AttendanceRecord.objects.filter(
                created_by=command.user,
                organization=command.organization,
                entry_datetime__gte=today_start_datetime,
            )
            .order_by("created_by", "-entry_datetime")
            .distinct("created_by")
        )

        if latest_user_attendance_records:
            users = [record.created_by for record in latest_user_attendance_records]

            organizationmembership_by_username = {
                m.user.username: m for m in OrganizationMembership.objects.filter(organization=command.organization, user__in=users)
            }

            user_status_blocks = []
            web_client = WebClient(token=command.organization.slack_api_token)
            for record in latest_user_attendance_records:
                user_organization_membership = organizationmembership_by_username.get(record.created_by.username, None)
                user_image_url = cls._get_user_image_url(web_client, user_organization_membership, refresh_days=settings.REFRESH_SLACK_IMAGE_URL_DAYS)

                local_created_datetime = record.created_datetime.astimezone(settings.JST)
                if user_image_url:
                    logger.debug(f"User {record.created_by.username} has slack_image_url: {user_image_url}")
                    # Output message with user SLACK image
                    user_status_blocks.append(
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "image",
                                    "image_url": user_image_url,
                                    "alt_text": record.created_by.display_name,
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*{record.created_by.display_name}* {record.category} {local_created_datetime}",
                                },
                            ],
                        }
                    )
                else:
                    logger.warning(f"User {record.created_by.username} has no slack_image_url: {user_image_url}")
                    # Output message WITHOUT user SLACK image, fallback to :white_square:
                    user_status_blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f":white_square: *{record.created_by.display_name}* {record.category} {local_created_datetime}",
                            },
                        }
                    )

            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"今日（{today_start_datetime}）の出勤記録:\n",
                    },
                }
            ]
            command_response_blocks.extend(user_status_blocks)

        else:
            logger.warning(f"No AttendenceRecord(s) found for organization {command.organization} on {today_start_datetime}")
            command_response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"今日（{today_start_datetime}）出勤記録がみつかりません。",
                    },
                }
            ]

        # Notify user that notification was sent to the registered channel
        webhook_client = WebhookClient(command.response_url)
        webhook_send_response = webhook_client.send(blocks=command_response_blocks, response_type=SlackResponseTypes.EPHEMERAL)

        return command_response_blocks, web_send_response, webhook_send_response
