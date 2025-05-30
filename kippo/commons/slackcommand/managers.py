import logging

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership, SlackCommand
from slack_sdk.web import SlackResponse
from slack_sdk.webhook import WebhookClient, WebhookResponse

from commons.definitions import SlackResponseTypes
from commons.slackcommand.base import SubCommandBase

from . import get_all_subcommands

logger = logging.getLogger(__name__)


class SlackCommandManager:
    # SUPPORTED_SUB_COMMANDS = (
    #     "開始",
    #     "clockin",
    #     "clock-in",
    #     "breakstart",
    #     "break-start",
    #     "離席",
    #     "breakend",
    #     "break-end"
    #     "再開",
    #     "作業再開",
    #     "終了",
    #     "終わり",
    #     "clockout",
    #     "clock-out",
    #     "AM半休",
    #     "PM半休",
    #     "半休",
    #     "set-holiday",
    #     "list-holidays",
    #     "cancel-holiday",
    #     "attendance-status",
    # )

    REQUIRED_ORGANIZATION_FIELDS = (
        "slack_api_token",
        "slack_signing_secret",
        "slack_command_name",
        "slack_attendance_report_channel",
    )

    def __init__(self, organization: KippoOrganization) -> None:
        """Initialize the SlackCommandManager with the organization command name."""
        self.organization = organization

        for field in self.REQUIRED_ORGANIZATION_FIELDS:
            if not getattr(self.organization, field):
                raise ValueError(f"Organization is missing required field: {field}")

        self.organization_command_name = organization.slack_command_name

        self.valid_subcommands = {}  # key by alias
        for command in get_all_subcommands():
            if hasattr(command, "ALIASES"):
                for alias in command.ALIASES:
                    self.valid_subcommands[alias] = command

    def _get_kippouser(self, slack_user_name: str) -> KippoUser | None:
        """Get the KippoUser associated with the given Slack user ID."""
        logger.debug(f"slack_user_name={slack_user_name}")
        membership = OrganizationMembership.objects.filter(organization=self.organization, slack_username=slack_user_name).first()
        if membership:
            return membership.user
        logger.error(f"OrganizationMembership not found in {self.organization.name}({self.organization.pk}) for slack_user_name={slack_user_name}")
        return None

    def _process_subcommand(
        self, sub_command_id: str, request_payload: dict
    ) -> tuple[list[dict], SlackResponse | None, WebhookResponse | None, WebhookResponse | None]:
        command_blocks = []
        web_send_response = None
        webhook_send_response = None
        error_response = None

        # ex)
        # user_id = UCCNXXXX
        # user_name = firstname.lastname
        response_url = request_payload.get("response_url", "")
        slack_user_name = request_payload.get("user_name", "")
        kippouser = self._get_kippouser(slack_user_name)
        if not kippouser:
            logger.error(
                f"kippouser not identified from slack_user_name={slack_user_name} in organization {self.organization.name}({self.organization.pk})"
            )
            if response_url:
                # Send a message to the response URL
                webhook = WebhookClient(response_url)
                error_response = webhook.send(
                    text=f"{self.organization.name} には、`{slack_user_name}`のユーザー設定がありません。",
                    response_type=SlackResponseTypes.EPHEMERAL,
                )
        else:
            logger.debug(f"Creating SlackCommand organization.name={self.organization.name}, username={kippouser.username} ...")
            slack_command = SlackCommand(
                organization=self.organization,
                user=kippouser,
                sub_command=sub_command_id,
                text=request_payload.get("text", ""),
                response_url=response_url,
                payload=request_payload,
            )
            slack_command.save()
            logger.debug(f"Creating SlackCommand organization.name={self.organization.name}, username={kippouser.username} ... DONE")
            sub_command: SubCommandBase | None = self.valid_subcommands.get(sub_command_id, None)
            if sub_command:
                # Call the handle method of the command class
                logger.info(f"Processing sub-command ({sub_command.__name__}) {sub_command_id} ...")
                command_blocks, web_send_response, webhook_send_response = sub_command.handle(slack_command)
                logger.debug(f"command_blocks={command_blocks}")
                if web_send_response:
                    logger.debug(
                        f"web_send_response.status_code={web_send_response.status_code}, web_send_response.data={repr(web_send_response.data)}"
                    )
                else:
                    logger.debug("web_send_response is None, no response Message posted to Slack")
                if webhook_send_response:
                    logger.debug(
                        f"webhook_send_response.status_code={webhook_send_response.status_code}, "
                        f"webhook_send_response.body={webhook_send_response.body}"
                    )
                else:
                    logger.debug(f"webhook_send_response is None, no response Message posted to User({slack_user_name})")
                logger.info(f"Processing sub-command ({sub_command.__name__}) {sub_command_id} ... DONE")
            else:
                logger.debug(f"valid_subcommands={self.valid_subcommands}")
                command_text = request_payload.get("text", "")
                logger.error(f"No sub-command recognized in the command text: {command_text}")
                if response_url:
                    # Send a message to the response URL
                    webhook = WebhookClient(response_url)
                    error_response = webhook.send(
                        text=f"Invalid sub-command. Supported sub-commands are: {', '.join(self.valid_subcommands)}",
                        response_type=SlackResponseTypes.EPHEMERAL,
                    )
        return command_blocks, web_send_response, webhook_send_response, error_response

    def process_command(self, request_payload: dict) -> tuple[list[dict], SlackResponse | None, WebhookResponse | None, WebhookResponse | None]:
        """Process the Slack command request."""
        command_blocks = []
        web_send_response = None
        webhook_send_response = None
        error_response = None

        # Extract the command and parameters from the request
        organization_command_name = self.organization.slack_command_name
        if not organization_command_name.startswith("/"):
            organization_command_name = f"/{organization_command_name}"
        request_command_name = request_payload.get("command", "")
        response_url = request_payload.get("response_url", "")
        logger.debug(f"request_command_name={request_command_name}, organization_command_name={organization_command_name}")
        if request_command_name == organization_command_name:
            command_text = request_payload.get("text", "")
            sub_command_id = command_text.split(" ")[0] if command_text else ""
            if sub_command_id:
                logger.debug(f"command_text={command_text}, sub_command_id={sub_command_id}")
                command_blocks, web_send_response, webhook_send_response, error_response = self._process_subcommand(sub_command_id, request_payload)
            else:
                logger.debug(f"valid_subcommands={self.valid_subcommands}")
                logger.error(f"Unable to parse sub_command_id from command_text: {command_text}")
                if response_url:
                    # Send a message to the response URL
                    webhook = WebhookClient(response_url)
                    error_response = webhook.send(
                        text=f"Invalid sub-command. Supported sub-commands are: {', '.join(self.valid_subcommands)}",
                        response_type=SlackResponseTypes.EPHEMERAL,
                    )

        else:
            logger.warning(f"Unknown command received: {request_command_name}")
            if response_url:
                # Send a message to the response URL
                webhook = WebhookClient(response_url)
                error_response = webhook.send(
                    text=f"Invalid sub-command. Supported sub-commands are: {', '.join(self.valid_subcommands)}",
                    response_type=SlackResponseTypes.EPHEMERAL,
                )

        return command_blocks, web_send_response, webhook_send_response, error_response
