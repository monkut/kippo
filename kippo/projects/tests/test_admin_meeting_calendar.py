"""Tests for KippoProjectAdmin MTG calendar link fields + Slack action (kiconiaworks/kippo#13)."""

from http import HTTPStatus
from unittest import mock

from accounts.models import KippoUser
from commons.tests import DEFAULT_FIXTURES, MockRequest, setup_basic_project
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import constants as message_constants
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpRequest
from django.test import RequestFactory, TestCase
from django.urls import reverse
from slack_sdk.errors import SlackApiError

from projects.admin import KippoProjectAdmin, add_calendar_links_to_slack_channels_action
from projects.exceptions import SlackChannelNotFoundError
from projects.models import KippoProject
from projects.tests.test_admin import KippoProjectAdminFixtureTestCaseBase

SET_PINNED_MESSAGE_PATH = "projects.slackcommand.managers.ProjectCalendarLinkManager.set_calendar_pinned_message"


def _request_with_messages(user: KippoUser) -> HttpRequest:
    request = RequestFactory().post("/admin/projects/kippoproject/")
    request.user = user
    request.session = {}  # dict satisfies FallbackStorage's session read/write contract
    request._messages = FallbackStorage(request)
    return request


class MeetingCalendarAdminFieldTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.project: KippoProject = created["KippoProject"]
        self.admin = KippoProjectAdmin(KippoProject, AdminSite())

    def test_meeting_calendar_url_field_renders_link_and_copy_button(self):
        html = self.admin.meeting_calendar_url_field(self.project)
        assert "kippo-copy-button" in html
        assert "data-clipboard-text" in html
        assert "calendar.google.com/calendar/render" in html  # url in href + data-clipboard-text
        assert "<a href=" in html
        assert "Create Project Meeting" in html  # link label, not the raw url
        assert 'class="kippo-copy-status"' in html  # aria-live status slot for copy feedback
        assert 'aria-live="polite"' in html

    def test_meeting_description_tag_field_renders_tag_and_copy_button(self):
        html = self.admin.meeting_description_tag_field(self.project)
        assert "kippo-copy-button" in html
        assert "data-clipboard-text" in html
        assert "dsearch" in html
        assert 'class="kippo-copy-status"' in html

    def test_meeting_fields_empty_for_unsaved_project(self):
        assert self.admin.meeting_calendar_url_field(None) == ""
        assert self.admin.meeting_calendar_url_field(KippoProject()) == ""
        assert self.admin.meeting_description_tag_field(None) == ""
        assert self.admin.meeting_description_tag_field(KippoProject()) == ""

    def test_meeting_fields_readonly_on_change_excluded_on_add(self):
        request = MockRequest()
        request.user = self.user
        readonly = self.admin.get_readonly_fields(request, self.project)
        assert "meeting_calendar_url_field" in readonly
        assert "meeting_description_tag_field" in readonly
        excluded = self.admin.get_exclude(request, None)
        assert "meeting_calendar_url_field" in excluded
        assert "meeting_description_tag_field" in excluded

    def test_meeting_fields_in_top_section_after_problem_definition_on_change(self):
        # The MTG-calendar readonly displays are surfaced at the top, directly below problem_definition,
        # not in the collapsed Details section.
        request = MockRequest()
        request.user = self.user
        fieldsets = self.admin.get_fieldsets(request, self.project)
        label, opts = fieldsets[0]
        top_fields = opts["fields"]
        assert label is None
        assert "meeting_calendar_url_field" in top_fields
        assert "meeting_description_tag_field" in top_fields
        assert top_fields.index("meeting_calendar_url_field") > top_fields.index("problem_definition")
        details = next(opts["fields"] for lbl, opts in fieldsets if str(lbl) == "Details")
        assert "meeting_calendar_url_field" not in details
        assert "meeting_description_tag_field" not in details


class AddCalendarLinksActionTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.organization = created["KippoOrganization"]
        self.project: KippoProject = created["KippoProject"]
        self.admin = KippoProjectAdmin(KippoProject, AdminSite())

    def _run_action(self) -> list:
        request = _request_with_messages(self.user)
        queryset = KippoProject.objects.filter(pk=self.project.pk)
        add_calendar_links_to_slack_channels_action(self.admin, request, queryset)
        return list(request._messages)

    @mock.patch(SET_PINNED_MESSAGE_PATH, return_value="added")
    def test_action_adds_link_success(self, mock_set: mock.MagicMock):
        self.organization.slack_api_token = "xoxb-test"  # noqa: S105
        self.organization.save()
        self.project.slack_channel_name = "project-alpha"
        self.project.save()
        messages = self._run_action()
        assert len(messages) == 1
        assert messages[0].level == message_constants.INFO
        assert self.project.name in messages[0].message

    def test_action_errors_when_no_slack_channel(self):
        self.project.slack_channel_name = ""
        self.project.save()
        messages = self._run_action()
        assert len(messages) == 1
        assert messages[0].level == message_constants.ERROR
        assert "no Slack conversation channel" in messages[0].message

    def test_action_errors_when_org_has_no_slack_token(self):
        self.organization.slack_api_token = ""
        self.organization.save()
        self.project.slack_channel_name = "project-alpha"
        self.project.save()
        messages = self._run_action()
        assert len(messages) == 1
        assert messages[0].level == message_constants.ERROR
        assert "no Slack API token" in messages[0].message

    @mock.patch(SET_PINNED_MESSAGE_PATH, side_effect=SlackChannelNotFoundError("nope"))
    def test_action_reports_channel_not_found(self, mock_set: mock.MagicMock):
        self.organization.slack_api_token = "xoxb-test"  # noqa: S105
        self.organization.save()
        self.project.slack_channel_name = "project-alpha"
        self.project.save()
        messages = self._run_action()
        assert len(messages) == 1
        assert messages[0].level == message_constants.ERROR
        assert "was not found in the workspace" in messages[0].message

    @mock.patch(SET_PINNED_MESSAGE_PATH, side_effect=SlackApiError("err", {"error": "missing_scope"}))
    def test_action_reports_slack_api_error(self, mock_set: mock.MagicMock):
        self.organization.slack_api_token = "xoxb-test"  # noqa: S105
        self.organization.save()
        self.project.slack_channel_name = "project-alpha"
        self.project.save()
        messages = self._run_action()
        assert len(messages) == 1
        assert messages[0].level == message_constants.ERROR
        assert "missing_scope" in messages[0].message


class MeetingCalendarChangeViewTestCase(KippoProjectAdminFixtureTestCaseBase):
    """Integration: the KippoProject change view renders the calendar copy buttons + clipboard script."""

    def test_change_view_renders_calendar_copy_fields_and_script(self):
        project = self.make_project("calendar-link-project")
        url = reverse("admin:projects_kippoproject_change", args=[project.id])
        response = self.client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        # readonly fields
        assert "kippo-copy-button" in content
        assert "calendar.google.com/calendar/render" in content
        assert "[dsearch]" in content
        # clipboard handler injected by change_form.html
        assert "コピーしました" in content
