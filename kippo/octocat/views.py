import json
from django.http import HttpResponseBadRequest
from .functions import process_incoming_project_card_event


def webhook(request):
    if request.method == 'POST':
        event_type = request.META['X_GITHUB_EVENT'].strip()
        if event_type == 'project_card':
            body = json.loads(request.body.decode('utf8'))
            process_incoming_project_card_event(body)
        else:
            return HttpResponseBadRequest(f'Unsupported event: {event_type}')
    return HttpResponseBadRequest('what are you talking about!?')