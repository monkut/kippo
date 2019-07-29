import json
import hmac
import logging
from django.shortcuts import get_object_or_404
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from .functions import process_incoming_project_card_event
from .models import KippoOrganization


logger = logging.getLogger(__name__)


def validate_webhook_request(request: HttpRequest, organization: KippoOrganization) -> True:
    """
    Validate the contents with the registered secret
    https://developer.github.com/webhooks/securing/#validating-payloads-from-github
    """
    secret = organization.webhook_secret.encode('utf8')
    body = request.body
    calculated_signature = hmac.new(secret + body).hexdigest()
    compare_sig = f'sha1={calculated_signature}'
    github_signature = request.META.get('X-Hub-Signature', None)
    if not github_signature:
        raise ValueError(f'"X-Hub-Signature" not supplied in header: {request.META}')

    result = False
    if github_signature == compare_sig:
        result = True
    else:
        msg = f'header({github_signature}) != calculated({compare_sig})'
        logger.debug(msg)
    return result


def webhook(request: HttpRequest, organization_id: str):
    logger.info('webhook request received')
    organization = get_object_or_404(KippoOrganization, pk=organization_id)

    try:
        is_validated = validate_webhook_request(request, organization)
    except ValueError as e:
        return HttpResponseBadRequest(*e.args)

    if not is_validated:
        return HttpResponseForbidden('Invalid "X-Hub-Signature"')

    if request.method == 'POST':
        # Github Webhook headers:
        # https://developer.github.com/webhooks/#delivery-headers
        #
        # Django converts incoming headers to a 'normalized' format, see:
        # https://docs.djangoproject.com/en/2.1/ref/request-response/#django.http.HttpRequest.META
        event_type = request.META['X_GITHUB_EVENT'].strip()
        if event_type == 'project_card':
            logger.debug(f'decoding webhook event_type: {event_type}')
            body = json.loads(request.body.decode('utf8'))
            logger.debug(f'processing webhook event_type: {event_type}')
            process_incoming_project_card_event(organization, body)
            return HttpResponse(status=201, content='201 Created')
        else:
            return HttpResponseBadRequest(f'Unsupported event: {event_type}')
    return HttpResponseBadRequest('what are you talking about!?')
