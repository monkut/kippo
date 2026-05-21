"""Tests for ProjectCalendarLinkManager Slack pinned-message management (kiconiaworks/kippo#13)."""

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

    @mock.patch("projects.slackcommand.managers.WebClient.pins_add", return_value={"ok": True})
    @mock.patch("projects.slackcommand.managers.WebClient.chat_postMessage", return_value={"ok": True, "ts": "1700000000.000100"})
    @mock.patch("projects.slackcommand.managers.WebClient.pins_list", return_value={"items": []})
    @mock.patch("projects.slackcommand.managers.WebClient.conversations_list", return_value=CHANNELS_RESPONSE)
    def test_set_calendar_pinned_message_adds_new(
        self,
        mock_conversations: mock.MagicMock,
        mock_pins_list: mock.MagicMock,
        mock_post: mock.MagicMock,
        mock_pins_add: mock.MagicMock,
    ):
        manager = ProjectCalendarLinkManager(self.organization)
        result = manager.set_calendar_pinned_message(self.project)
        assert result == "added"
        mock_post.assert_called_once()
        _, post_kwargs = mock_post.call_args
        assert post_kwargs["channel"] == "C0ALPHA"
        assert self.project.get_meeting_calendar_template_url() in post_kwargs["text"]
        assert ProjectCalendarLinkManager.CALENDAR_MESSAGE_TITLE in post_kwargs["text"]
        mock_pins_add.assert_called_once()
        _, pin_kwargs = mock_pins_add.call_args
        assert pin_kwargs["channel"] == "C0ALPHA"
        assert pin_kwargs["timestamp"] == "1700000000.000100"

    @mock.patch("projects.slackcommand.managers.WebClient.chat_update", return_value={"ok": True})
    @mock.patch("projects.slackcommand.managers.WebClient.pins_list")
    @mock.patch("projects.slackcommand.managers.WebClient.conversations_list", return_value=CHANNELS_RESPONSE)
    def test_set_calendar_pinned_message_updates_existing(
        self,
        mock_conversations: mock.MagicMock,
        mock_pins_list: mock.MagicMock,
        mock_update: mock.MagicMock,
    ):
        existing_text = f":calendar: <https://calendar.google.com/old|{ProjectCalendarLinkManager.CALENDAR_MESSAGE_TITLE}>"
        mock_pins_list.return_value = {"items": [{"type": "message", "message": {"ts": "1699999999.000200", "text": existing_text}}]}
        manager = ProjectCalendarLinkManager(self.organization)
        result = manager.set_calendar_pinned_message(self.project)
        assert result == "updated"
        mock_update.assert_called_once()
        _, kwargs = mock_update.call_args
        assert kwargs["channel"] == "C0ALPHA"
        assert kwargs["ts"] == "1699999999.000200"
        assert self.project.get_meeting_calendar_template_url() in kwargs["text"]

    @mock.patch("projects.slackcommand.managers.WebClient.conversations_list", return_value=CHANNELS_RESPONSE)
    def test_set_calendar_pinned_message_channel_not_found(self, mock_conversations: mock.MagicMock):
        self.project.slack_channel_name = "nonexistent-channel"
        self.project.save()
        manager = ProjectCalendarLinkManager(self.organization)
        with self.assertRaises(SlackChannelNotFoundError):
            manager.set_calendar_pinned_message(self.project)

    def test_set_calendar_pinned_message_requires_channel_name(self):
        self.project.slack_channel_name = ""
        self.project.save()
        manager = ProjectCalendarLinkManager(self.organization)
        with self.assertRaises(ValueError):
            manager.set_calendar_pinned_message(self.project)
