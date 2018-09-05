from typing import Dict
from django.http.request import HttpRequest
from django.conf import settings


def global_view_additional_context(request: HttpRequest) -> Dict:
    """
    context defined here is provided additionally to the template rendering contexxt

    :param request:
    :return:
    """
    context = {
        'URL_PREFIX': settings.URL_PREFIX,
        'STATIC_URL': settings.STATIC_URL,
        'DISPLAY_ADMIN_AUTH_FOR_MODELBACKEND': settings.DISPLAY_ADMIN_AUTH_FOR_MODELBACKEND,
    }
    return context
