"""Tests for ProjectCalendarLinkManager Slack channel bookmark management (kiconiaworks/kippo#13)."""

from unittest import mock

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase

from projects.exceptions import SlackChannelNotFoundError
from projects.models import KippoProject
from projects.slackcommand.managers import ProjectCalendarLinkManager

CHANNELS_RESPONSE = {
    "channels": [{"name": "project-alpha", "id": "C0ALPHA"}],
    "response_metadata": {"next_cursor": ""},
}


class ProjectCalendarLinkManagerTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.organization.slack_api_token = "xoxb-test-token"  # noqa: S105
        self.organization.save()
        self.project: KippoProject = created["KippoProject"]
        self.project.slack_channel_name = "project-alpha"
        self.project.save()

    def test_init_requires_slack_api_token(self):
        self.organization.slack_api_token = ""
        with self.assertRaises(ValueError):
            ProjectCalendarLinkManager(self.organization)

    @mock.patch("projects.slackcommand.managers.WebClient.bookmarks_add", return_value={"ok": True})
    @mock.patch("projects.slackcommand.managers.WebClient.bookmarks_list", return_value={"bookmarks": []})
    @mock.patch("projects.slackcommand.managers.WebClient.conversations_list", return_value=CHANNELS_RESPONSE)
    def test_set_calendar_bookmark_adds_new(self, mock_conversations: mock.MagicMock, mock_list: mock.MagicMock, mock_add: mock.MagicMock):
        manager = ProjectCalendarLinkManager(self.organization)
        result = manager.set_calendar_bookmark(self.project)
        assert result == "added"
        mock_add.assert_called_once()
        _, kwargs = mock_add.call_args
        assert kwargs["channel_id"] == "C0ALPHA"
        assert kwargs["link"] == self.project.get_meeting_calendar_template_url()
        assert kwargs["title"] == ProjectCalendarLinkManager.CALENDAR_BOOKMARK_TITLE

    @mock.patch("projects.slackcommand.managers.WebClient.bookmarks_edit", return_value={"ok": True})
    @mock.patch("projects.slackcommand.managers.WebClient.bookmarks_list")
    @mock.patch("projects.slackcommand.managers.WebClient.conversations_list", return_value=CHANNELS_RESPONSE)
    def test_set_calendar_bookmark_updates_existing(self, mock_conversations: mock.MagicMock, mock_list: mock.MagicMock, mock_edit: mock.MagicMock):
        mock_list.return_value = {"bookmarks": [{"id": "Bk1", "title": ProjectCalendarLinkManager.CALENDAR_BOOKMARK_TITLE}]}
        manager = ProjectCalendarLinkManager(self.organization)
        result = manager.set_calendar_bookmark(self.project)
        assert result == "updated"
        mock_edit.assert_called_once()
        _, kwargs = mock_edit.call_args
        assert kwargs["bookmark_id"] == "Bk1"
        assert kwargs["link"] == self.project.get_meeting_calendar_template_url()

    @mock.patch("projects.slackcommand.managers.WebClient.conversations_list", return_value=CHANNELS_RESPONSE)
    def test_set_calendar_bookmark_channel_not_found(self, mock_conversations: mock.MagicMock):
        self.project.slack_channel_name = "nonexistent-channel"
        self.project.save()
        manager = ProjectCalendarLinkManager(self.organization)
        with self.assertRaises(SlackChannelNotFoundError):
            manager.set_calendar_bookmark(self.project)

    def test_set_calendar_bookmark_requires_channel_name(self):
        self.project.slack_channel_name = ""
        self.project.save()
        manager = ProjectCalendarLinkManager(self.organization)
        with self.assertRaises(ValueError):
            manager.set_calendar_bookmark(self.project)
