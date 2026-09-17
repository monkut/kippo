from django.contrib import admin as django_admin
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
from django.test import RequestFactory, TestCase
from social_django.middleware import SocialAuthExceptionMiddleware
from social_django.utils import load_backend, load_strategy

from accounts.exceptions import OrganizationInviteExpiredError

_NO_RESPONSE = lambda _request: None  # noqa: E731 (middleware get_response stub)


class ExpiredInviteLoginErrorTestCase(TestCase):
    """The expired-invite pipeline error is redirected to the admin login page and rendered there as a message."""

    def _build_social_request(self) -> HttpRequest:
        request = RequestFactory().get("/complete/google-oauth2/")
        request.user = AnonymousUser()
        SessionMiddleware(_NO_RESPONSE).process_request(request)
        MessageMiddleware(_NO_RESPONSE).process_request(request)
        request.social_strategy = load_strategy(request)
        request.backend = load_backend(request.social_strategy, "google-oauth2", redirect_uri=None)
        return request

    def test_expired_invite_error_redirects_to_login_with_message(self):
        request = self._build_social_request()
        error = OrganizationInviteExpiredError(request.backend, "Your invitation to test-org expired on 2026-09-10.")

        response = SocialAuthExceptionMiddleware(_NO_RESPONSE).process_exception(request, error)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/admin/login/"), response.url)
        stored = [str(m) for m in get_messages(request)]
        self.assertEqual(stored, [str(error)])

    def test_login_page_renders_message(self):
        request = self._build_social_request()
        error = OrganizationInviteExpiredError(request.backend, "Your invitation to test-org expired on 2026-09-10.")
        SocialAuthExceptionMiddleware(_NO_RESPONSE).process_exception(request, error)

        # same request/session carries the queued message into the rendered login page
        request.path = request.path_info = "/admin/login/"
        request.method = "GET"
        response = django_admin.site.login(request)
        response.render()
        self.assertContains(response, "Your invitation to test-org expired on 2026-09-10.")
