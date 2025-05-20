import datetime
import hmac
import logging
import time
from collections import Counter, defaultdict
from http import HTTPStatus

from commons.slackcommand.managers import SlackCommandManager
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib import messages
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    request as DjangoRequest,  # noqa: N812
)
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from projects.functions import get_user_session_organization
from slack_sdk.signature import SignatureVerifier
from zappa.asynchronous import task

from .models import KippoOrganization, OrganizationMembership

logger = logging.getLogger(__name__)


def _get_organization_monthly_available_workdays(organization: KippoOrganization) -> tuple[list[OrganizationMembership], dict[str, Counter]]:
    # get organization memberships
    organization_memberships = list(
        OrganizationMembership.objects.filter(organization=organization, user__github_login__isnull=False, is_developer=True)
        .exclude(user__github_login__startswith=settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX)
        .order_by("user__github_login")
    )
    member_personal_holiday_dates = {m.user.github_login: tuple(m.user.personal_holiday_dates()) for m in organization_memberships}
    member_public_holiday_dates = {m.user.github_login: tuple(m.user.public_holiday_dates()) for m in organization_memberships}

    current_datetime = timezone.now()
    start_datetime = datetime.datetime(current_datetime.year, current_datetime.month, 1, tzinfo=datetime.UTC)

    # get the last full month 2 years from now
    end_datetime = start_datetime + relativedelta(months=1, years=2)
    end_datetime = end_datetime.replace(day=1)

    current_date = start_datetime.date()
    end_date = end_datetime.date()

    monthly_available_workdays = defaultdict(Counter)
    while current_date < end_date:
        month_key = current_date.strftime("%Y-%m")
        for membership in organization_memberships:
            if (
                current_date not in member_personal_holiday_dates[membership.user.github_login]
                and current_date not in member_public_holiday_dates[membership.user.github_login]
            ) and current_date.weekday() in membership.committed_weekdays:
                monthly_available_workdays[month_key][membership.user] += 1
        current_date += datetime.timedelta(days=1)
    return organization_memberships, monthly_available_workdays


def view_organization_members(request: DjangoRequest):
    try:
        selected_organization, user_organizations = get_user_session_organization(request)
    except ValueError as e:
        return HttpResponseBadRequest(str(e.args))

    organization_memberships, monthly_available_workdays = _get_organization_monthly_available_workdays(selected_organization)

    # prepare monthly output for template
    monthly_member_data = []
    for month in sorted(monthly_available_workdays.keys()):
        data = (
            month,
            sum(monthly_available_workdays[month].values()),
            [monthly_available_workdays[month][m.user] for m in organization_memberships],  # get data in organization_membership order
        )
        monthly_member_data.append(data)

    context = {
        "selected_organization": selected_organization,
        "organizations": user_organizations,
        "organization_memberships": organization_memberships,
        "monthly_available_workdays": monthly_member_data,
        "messages": messages.get_messages(request),
    }

    return render(request, "accounts/view_organization_members.html", context)


def _validate_slack_request(body: str, header_timestamp: int, header_signature: str, organization: KippoOrganization) -> bool:
    signature_verifier = SignatureVerifier(
        signing_secret=organization.slack_signing_secret,
    )

    is_valid = False
    if not all((header_timestamp, header_signature)):
        logger.error("Missing X-Slack-Request-Timestamp or X-Slack-Signature header")
    else:
        # Validate the request
        # https://api.slack.com/authentication/verifying-requests-from-slack
        five_minutes_in_seconds = 60 * 5
        if abs(time.time() - header_timestamp) > five_minutes_in_seconds:
            logger.error(f"Request received {five_minutes_in_seconds}s after X-Slack-Request-Timestamp")
        else:
            try:
                calculated_signature = signature_verifier.generate_signature(timestamp=str(header_timestamp), body=body)
                logger.debug(f"calculated_signature={calculated_signature}, header_signature={header_signature}")
                is_valid = hmac.compare_digest(calculated_signature, header_signature)
            except ValueError:
                logger.exception("signature_verifier.is_valid() failed!")
    return is_valid


@task
def handle_slack_command(organization_id: str, request_body: str, header_timestamp: int, header_signature: str, request_payload: dict):
    """Handle command requests in separate process."""
    logger.debug(f"organization_id={organization_id}, request_payload={request_payload}")
    organization = KippoOrganization.objects.filter(id=organization_id).first()
    if not organization:
        logger.error(f"Organization {organization_id} not found")
        return

    # Check that KippoOrganization is properly configured to use the Slack Command
    missing_fields = [f for f in SlackCommandManager.REQUIRED_ORGANIZATION_FIELDS if not getattr(organization, f)]
    if missing_fields:
        error_message = f"Organization {organization.name}({organization_id}) is missing required field(s): {missing_fields}"
        logger.error(error_message)
        return

    is_valid = _validate_slack_request(
        body=request_body,
        header_timestamp=header_timestamp,
        header_signature=header_signature,
        organization=organization,
    )
    if not is_valid:
        logger.error(f"Invalid Slack request for organization {organization_id}")
        return

    manager = SlackCommandManager(organization=organization)
    logger.debug("Processing Slack command ...")
    manager.process_command(request_payload)
    logger.debug("Processing Slack command ... DONE")


@csrf_exempt
def organization_slack_webhook(request: DjangoRequest, organization_id: str):
    """Handle Slack command requests."""
    logger.debug(f"organization_slack_webhook: organization_id={organization_id}, request.POST={request.POST}")

    if request.method == "POST":
        # performing validation and checking in separate process, handle_slack_command(),
        # to avoid timeout response from Slack
        header_timestamp_str = request.META.get("HTTP_X_SLACK_REQUEST_TIMESTAMP", "")
        header_timestamp = int(float(header_timestamp_str.strip()))
        header_signature = request.META.get("HTTP_X_SLACK_SIGNATURE", "")
        handle_slack_command(
            organization_id=str(organization_id),
            request_body=request.body.decode("utf-8"),
            header_timestamp=header_timestamp,
            header_signature=header_signature,
            request_payload=request.POST.dict(),
        )
        return HttpResponse(status=HTTPStatus.OK)
    return HttpResponseNotAllowed(permitted_methods=("POST",))
