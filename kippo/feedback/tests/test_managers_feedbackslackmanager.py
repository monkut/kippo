"""Tests for FeedbackSlackManager."""

from unittest import mock

from accounts.models import KippoOrganization, KippoUser
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from django.utils import timezone

from ..definitions import FeedbackCategories
from ..models import Feedback
from ..slackcommand.managers import (
    UNCATEGORIZED_COMPONENT,
    FeedbackSlackManager,
    infer_feedback_component,
)


class InferFeedbackComponentTestCase(TestCase):
    def _make(self, title: str) -> Feedback:
        return Feedback(title=title, comment="x", category=FeedbackCategories.GENERAL.value)

    def test_bracketed_prefix(self):
        assert infer_feedback_component(self._make("[UI] dropdown is broken")) == "UI"

    def test_bracketed_prefix_lowercase(self):
        assert infer_feedback_component(self._make("[api] /v1/feedback returns 500")) == "api"

    def test_leading_whitespace_ok(self):
        assert infer_feedback_component(self._make("   [admin] filter not applied")) == "admin"

    def test_no_brackets_is_uncategorized(self):
        assert infer_feedback_component(self._make("dark mode toggle missing")) == UNCATEGORIZED_COMPONENT

    def test_empty_brackets_is_uncategorized(self):
        assert infer_feedback_component(self._make("[] empty bracket")) == UNCATEGORIZED_COMPONENT

    def test_bracket_not_at_start_is_uncategorized(self):
        assert infer_feedback_component(self._make("dropdown [UI] broken")) == UNCATEGORIZED_COMPONENT


class FeedbackSlackManagerTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization: KippoOrganization = created["KippoOrganization"]
        self.user: KippoUser = created["KippoUser"]

        self.organization.enable_slack_channel_reporting = True
        self.organization.slack_api_token = "xoxb-test-token"  # noqa: S105
        self.organization.slack_signing_secret = "signing-secret"  # noqa: S105
        self.organization.slack_bot_name = "kippo-bot"
        self.organization.slack_channel_name = "#kippo"
        self.organization.slack_weekly_project_report_channel = "#kippo-feedback"
        self.organization.save()

    def _create_feedback(self, title: str, created_offset_days: int = 1, category: str = FeedbackCategories.GENERAL.value) -> Feedback:
        feedback = Feedback.objects.create(
            organization=self.organization,
            category=category,
            title=title,
            comment="some comment",
            created_by=self.user,
            updated_by=self.user,
        )
        # backdate the auto-now created_datetime so it falls inside the 7-day window
        backdated = timezone.now() - timezone.timedelta(days=created_offset_days)
        Feedback.objects.filter(pk=feedback.pk).update(created_datetime=backdated)
        feedback.refresh_from_db()
        return feedback

    def test_init_requires_enable_flag(self):
        self.organization.enable_slack_channel_reporting = False
        self.organization.save()
        with self.assertRaises(ValueError):
            FeedbackSlackManager(organization=self.organization)

    @mock.patch("feedback.slackcommand.managers.WebClient.chat_postMessage", return_value={"ok": True})
    def test_no_feedback_posts_empty_summary(self, mock_post: mock.MagicMock):
        manager = FeedbackSlackManager(organization=self.organization)
        block_groups, _ = manager.post_weekly_feedback_summary()

        assert len(block_groups) == 1
        only_group = block_groups[0]
        # header + divider + "no feedback" section
        assert only_group[0]["type"] == "header"
        assert "_No feedback received" in only_group[-1]["text"]["text"]
        assert mock_post.call_count == 1

    @mock.patch("feedback.slackcommand.managers.WebClient.chat_postMessage", return_value={"ok": True})
    def test_feedback_grouped_by_component(self, mock_post: mock.MagicMock):
        self._create_feedback("[UI] dropdown broken")
        self._create_feedback("[UI] color contrast low")
        self._create_feedback("[api] /v1 500")
        self._create_feedback("no component prefix")

        manager = FeedbackSlackManager(organization=self.organization)
        block_groups, _ = manager.post_weekly_feedback_summary()

        # one message group expected (well under 50 blocks)
        assert len(block_groups) == 1
        blocks = block_groups[0]

        header_texts = [b["text"]["text"] for b in blocks if b["type"] == "header"]
        # summary header + 3 component headers (UI, api, uncategorized) sorted alphabetically
        assert any("Feedback Summary" in t for t in header_texts)
        component_headers = [t for t in header_texts if "Feedback Summary" not in t]
        assert component_headers[0].startswith("UI ")
        assert "(2)" in component_headers[0]
        assert any(h.startswith("api ") and "(1)" in h for h in component_headers)
        assert any(h.startswith(f"{UNCATEGORIZED_COMPONENT} ") and "(1)" in h for h in component_headers)
        assert mock_post.call_count == 1

    @mock.patch("feedback.slackcommand.managers.WebClient.chat_postMessage", return_value={"ok": True})
    def test_old_feedback_excluded(self, mock_post: mock.MagicMock):
        self._create_feedback("[old] way too old", created_offset_days=30)
        self._create_feedback("[fresh] inside window", created_offset_days=2)

        manager = FeedbackSlackManager(organization=self.organization)
        block_groups, _ = manager.post_weekly_feedback_summary()

        all_text = "\n".join(b.get("text", {}).get("text", "") for group in block_groups for b in group if isinstance(b.get("text", {}), dict))
        assert "fresh" in all_text
        assert "way too old" not in all_text
        assert mock_post.call_count == 1

    @mock.patch("feedback.slackcommand.managers.WebClient.chat_postMessage", return_value={"ok": True})
    def test_other_organization_excluded(self, mock_post: mock.MagicMock):
        other_org = KippoOrganization.objects.create(
            name="other",
            github_organization_name="otherorg",
            created_by=self.user,
            updated_by=self.user,
        )
        Feedback.objects.create(
            organization=other_org,
            category=FeedbackCategories.GENERAL.value,
            title="[leak] should not appear",
            comment="x",
            created_by=self.user,
            updated_by=self.user,
        )
        self._create_feedback("[mine] should appear")

        manager = FeedbackSlackManager(organization=self.organization)
        block_groups, _ = manager.post_weekly_feedback_summary()
        all_text = "\n".join(b.get("text", {}).get("text", "") for group in block_groups for b in group if isinstance(b.get("text", {}), dict))
        assert "should appear" in all_text
        assert "should not appear" not in all_text
        assert mock_post.call_count == 1
