"""Tests for the run_weeklyfeedbacksummary zappa handler."""

import json
from unittest import mock

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from django.utils import timezone

from ..definitions import FeedbackCategories
from ..handlers.functions import run_weeklyfeedbacksummary
from ..models import Feedback


class RunWeeklyFeedbackSummaryTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.organization.enable_slack_channel_reporting = True
        self.organization.slack_api_token = "xoxb-test-token"  # noqa: S105
        self.organization.slack_signing_secret = "signing-secret"  # noqa: S105
        self.organization.slack_bot_name = "kippo-bot"
        self.organization.slack_channel_name = "#kippo"
        self.organization.slack_weekly_project_report_channel = "#kippo-feedback"
        self.organization.save()

    def _create_feedback(self, title: str, days_ago: int = 1) -> Feedback:
        feedback = Feedback.objects.create(
            organization=self.organization,
            category=FeedbackCategories.BUG.value,
            title=title,
            comment="comment body",
            created_by=self.user,
            updated_by=self.user,
        )
        backdated = timezone.now() - timezone.timedelta(days=days_ago)
        Feedback.objects.filter(pk=feedback.pk).update(created_datetime=backdated)
        feedback.refresh_from_db()
        return feedback

    @mock.patch("feedback.slackcommand.managers.WebClient.chat_postMessage", return_value={"ok": True})
    def test_run_weeklyfeedbacksummary_serializable(self, *_):
        self._create_feedback("[UI] dropdown")
        self._create_feedback("plain title with no bracket")

        blocks = run_weeklyfeedbacksummary(event={}, context={})
        self.assertTrue(blocks)
        try:
            json.dumps(blocks, ensure_ascii=False)
        except TypeError as e:
            self.fail(f"Blocks cannot be serialized to JSON: {e}")

    @mock.patch("feedback.slackcommand.managers.WebClient.chat_postMessage", return_value={"ok": True})
    def test_run_weeklyfeedbacksummary_skips_orgs_without_reporting(self, mock_post: mock.MagicMock):
        self.organization.enable_slack_channel_reporting = False
        self.organization.save()

        self._create_feedback("[UI] dropdown")
        blocks = run_weeklyfeedbacksummary(event={}, context={})
        assert blocks == []
        assert mock_post.call_count == 0
