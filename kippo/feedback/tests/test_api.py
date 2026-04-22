"""Tests for the feedback REST API endpoints."""

from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ..definitions import FeedbackCategories, FeedbackReviewActions
from ..models import Feedback

API_PREFIX = f"{settings.URL_PREFIX}/api/feedback"


class FeedbackAPITestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization: KippoOrganization = created["KippoOrganization"]
        self.user: KippoUser = created["KippoUser"]

        self.other_user = KippoUser.objects.create(username="other_user", email="other@example.com")

        self.superuser = KippoUser.objects.create(
            username="super_reviewer",
            email="super@example.com",
            is_superuser=True,
            is_staff=True,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_feedback(self):
        url = f"{API_PREFIX}/feedback/"
        payload = {
            "category": FeedbackCategories.BUG.value,
            "title": "Login button broken",
            "comment": "Clicking it does nothing.",
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        data = response.json()
        self.assertEqual(data["title"], "Login button broken")
        self.assertEqual(data["category"], "bug")
        self.assertEqual(str(data["created_by"]), str(self.user.pk))
        self.assertEqual(str(data["organization"]), str(self.organization.pk))

    def test_create_default_category(self):
        url = f"{API_PREFIX}/feedback/"
        response = self.client.post(url, {"title": "x", "comment": "y"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        self.assertEqual(response.json()["category"], FeedbackCategories.GENERAL.value)

    def test_non_superuser_cannot_set_review_fields_on_create(self):
        url = f"{API_PREFIX}/feedback/"
        payload = {
            "title": "t",
            "comment": "c",
            "reviewed_datetime": timezone.now().isoformat(),
            "review_action": FeedbackReviewActions.ACCEPTED.value,
            "github_issue_url": "https://github.com/org/repo/issues/1",
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        feedback = Feedback.objects.get(pk=response.json()["id"])
        self.assertIsNone(feedback.reviewed_datetime)
        self.assertIsNone(feedback.review_action)
        self.assertIsNone(feedback.github_issue_url)

    def test_superuser_can_set_review_fields(self):
        feedback = Feedback.objects.create(
            title="t",
            comment="c",
            created_by=self.user,
            updated_by=self.user,
            organization=self.organization,
        )
        self.client.force_authenticate(user=self.superuser)
        url = f"{API_PREFIX}/feedback/{feedback.pk}/"
        now = timezone.now()
        payload = {
            "title": feedback.title,
            "comment": feedback.comment,
            "reviewed_datetime": now.isoformat(),
            "review_action": FeedbackReviewActions.ISSUE_CREATED.value,
            "github_issue_url": "https://github.com/org/repo/issues/42",
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK, response.content)
        feedback.refresh_from_db()
        self.assertIsNotNone(feedback.reviewed_datetime)
        self.assertEqual(feedback.review_action, FeedbackReviewActions.ISSUE_CREATED.value)
        self.assertEqual(feedback.github_issue_url, "https://github.com/org/repo/issues/42")

    def test_list_filters_to_own_org(self):
        Feedback.objects.create(
            title="mine",
            comment="c",
            created_by=self.user,
            updated_by=self.user,
            organization=self.organization,
        )

        other_org = KippoOrganization.objects.create(
            name="other-org",
            github_organization_name="other",
            created_by=self.user,
            updated_by=self.user,
        )
        Feedback.objects.create(
            title="not-mine",
            comment="c",
            created_by=self.other_user,
            updated_by=self.other_user,
            organization=other_org,
        )

        url = f"{API_PREFIX}/feedback/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        results = response.json().get("results", response.json())
        titles = [item["title"] for item in results]
        self.assertIn("mine", titles)
        self.assertNotIn("not-mine", titles)

    def test_user_sees_own_feedback_even_without_org(self):
        Feedback.objects.create(
            title="own-no-org",
            comment="c",
            created_by=self.user,
            updated_by=self.user,
            organization=None,
        )
        url = f"{API_PREFIX}/feedback/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        results = response.json().get("results", response.json())
        titles = [item["title"] for item in results]
        self.assertIn("own-no-org", titles)

    def test_superuser_sees_all(self):
        Feedback.objects.create(
            title="a",
            comment="c",
            created_by=self.user,
            updated_by=self.user,
            organization=self.organization,
        )
        other_org = KippoOrganization.objects.create(
            name="xorg",
            github_organization_name="xorg",
            created_by=self.user,
            updated_by=self.user,
        )
        Feedback.objects.create(
            title="b",
            comment="c",
            created_by=self.other_user,
            updated_by=self.other_user,
            organization=other_org,
        )
        self.client.force_authenticate(user=self.superuser)
        response = self.client.get(f"{API_PREFIX}/feedback/")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        results = response.json().get("results", response.json())
        self.assertEqual(len(results), 2)

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(f"{API_PREFIX}/feedback/")
        self.assertIn(response.status_code, (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN))

    def test_user_with_no_org_create_does_not_crash(self):
        lone_user = KippoUser.objects.create(username="lone", email="lone@example.com")
        self.client.force_authenticate(user=lone_user)
        response = self.client.post(
            f"{API_PREFIX}/feedback/",
            {"title": "t", "comment": "c"},
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.content)
        self.assertIsNone(response.json()["organization"])

    def test_non_creator_teammate_cannot_update(self):
        feedback = Feedback.objects.create(
            title="orig",
            comment="c",
            created_by=self.user,
            updated_by=self.user,
            organization=self.organization,
        )
        teammate = KippoUser.objects.create(username="teammate2", email="t2@example.com")
        OrganizationMembership.objects.create(
            user=teammate,
            organization=self.organization,
            created_by=self.user,
            updated_by=self.user,
        )
        self.client.force_authenticate(user=teammate)
        response = self.client.patch(
            f"{API_PREFIX}/feedback/{feedback.pk}/",
            {"title": "hijacked"},
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN, response.content)
        feedback.refresh_from_db()
        self.assertEqual(feedback.title, "orig")

    def test_non_creator_teammate_cannot_delete(self):
        feedback = Feedback.objects.create(
            title="keep",
            comment="c",
            created_by=self.user,
            updated_by=self.user,
            organization=self.organization,
        )
        teammate = KippoUser.objects.create(username="teammate3", email="t3@example.com")
        OrganizationMembership.objects.create(
            user=teammate,
            organization=self.organization,
            created_by=self.user,
            updated_by=self.user,
        )
        self.client.force_authenticate(user=teammate)
        response = self.client.delete(f"{API_PREFIX}/feedback/{feedback.pk}/")
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN, response.content)
        self.assertTrue(Feedback.objects.filter(pk=feedback.pk).exists())

    def test_other_user_in_same_org_can_see_feedback(self):
        Feedback.objects.create(
            title="visible",
            comment="c",
            created_by=self.user,
            updated_by=self.user,
            organization=self.organization,
        )
        teammate = KippoUser.objects.create(username="teammate", email="t@example.com")
        OrganizationMembership.objects.create(
            user=teammate,
            organization=self.organization,
            created_by=self.user,
            updated_by=self.user,
        )
        self.client.force_authenticate(user=teammate)
        response = self.client.get(f"{API_PREFIX}/feedback/")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        results = response.json().get("results", response.json())
        titles = [item["title"] for item in results]
        self.assertIn("visible", titles)
