import json
import logging
from django.http import HttpResponse, HttpResponseBadRequest
from .functions import process_incoming_project_card_event


logger = logging.getLogger(__name__)


def webhook(request):
    logger.info('webhook request received')
    if request.method == 'POST':
        # Github Webhook headers:
        # https://developer.github.com/webhooks/#delivery-headers
        #
        # Django converts incoming headers to a 'normalized' format, see:
        # https://docs.djangoproject.com/en/2.1/ref/request-response/#django.http.HttpRequest.META
        event_type = request.META['X_GITHUB_EVENT'].strip()
        if event_type == 'project_card':
            body = json.loads(request.body.decode('utf8'))
            process_incoming_project_card_event(body)
            return HttpResponse(status=201, content='201 Created')
        else:
            return HttpResponseBadRequest(f'Unsupported event: {event_type}')
    return HttpResponseBadRequest('what are you talking about!?')