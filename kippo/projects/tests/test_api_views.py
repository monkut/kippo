"""Tests for the projects API views."""

import datetime
import json
from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import Client, TestCase

from ..models import ProjectWeeklyEffort


class ProjectStatusApiTestCase(TestCase):
    """Test cases for the project status API endpoint."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Set project dates and allocated staff days
        self.project.start_date = datetime.date(2024, 1, 1)
        self.project.target_date = datetime.date(2024, 3, 31)
        self.project.allocated_staff_days = 60
        self.project.save()

        # Create another organization
        self.other_organization = KippoOrganization.objects.create(
            name="other-test-organization",
            github_organization_name="other-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # Create a user that doesn't belong to the project's organization
        self.other_user = KippoUser.objects.create(
            username="otheruser",
            github_login="otheruser",
            email="otheruser@example.com",
            is_staff=True,
        )
        OrganizationMembership.objects.create(
            user=self.other_user,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )

        # Create ProjectWeeklyEffort entries
        self.week1_start = datetime.date(2024, 1, 1)
        self.week2_start = datetime.date(2024, 1, 8)
        self.week3_start = datetime.date(2024, 1, 15)

        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=self.week1_start,
            hours=40,
            created_by=self.user,
            updated_by=self.user,
        )
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=self.week2_start,
            hours=35,
            created_by=self.user,
            updated_by=self.user,
        )
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=self.week3_start,
            hours=38,
            created_by=self.user,
            updated_by=self.user,
        )

        self.client = Client()

    def test_api_returns_project_data(self):
        """Test that API returns basic project information."""
        self.client.force_login(self.user)
        url = f"{settings.URL_PREFIX}/projects/api/project/{self.project.id}/status/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json.loads(response.content)

        # Check project data
        self.assertIn("project", data)
        self.assertEqual(data["project"]["id"], str(self.project.id))
        self.assertEqual(data["project"]["name"], self.project.name)
        self.assertEqual(data["project"]["start_date"], "2024-01-01")
        self.assertEqual(data["project"]["target_date"], "2024-03-31")
        self.assertEqual(data["project"]["allocated_staff_days"], 60)
        self.assertIsNotNone(data["project"]["allocated_effort_hours"])

    def test_api_returns_weekly_effort_data(self):
        """Test that API returns weekly effort data for all dates."""
        self.client.force_login(self.user)
        url = f"{settings.URL_PREFIX}/projects/api/project/{self.project.id}/status/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json.loads(response.content)

        # Check weekly effort data
        self.assertIn("weekly_effort", data)
        self.assertEqual(len(data["weekly_effort"]), 3)

        # Verify first week data
        week1_data = data["weekly_effort"][0]
        self.assertEqual(week1_data["week_start"], "2024-01-01")
        self.assertEqual(week1_data["user"], self.user.display_name)
        self.assertEqual(week1_data["user_display_name"], self.user.display_name)
        self.assertEqual(week1_data["hours"], 40)
        self.assertEqual(week1_data["cumulative_hours"], 40)

        # Verify cumulative values increase
        week2_data = data["weekly_effort"][1]
        self.assertEqual(week2_data["cumulative_hours"], 75)  # 40 + 35

        week3_data = data["weekly_effort"][2]
        self.assertEqual(week3_data["cumulative_hours"], 113)  # 40 + 35 + 38

    def test_api_returns_expected_effort_by_date(self):
        """Test that API returns expected effort calculations for each date."""
        self.client.force_login(self.user)
        url = f"{settings.URL_PREFIX}/projects/api/project/{self.project.id}/status/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json.loads(response.content)

        # Check expected effort data
        self.assertIn("expected_effort_by_date", data)
        self.assertEqual(len(data["expected_effort_by_date"]), 3)

        # Each entry should have date, expected_effort_days, and expected_effort_hours
        for effort_data in data["expected_effort_by_date"]:
            self.assertIn("date", effort_data)
            self.assertIn("expected_effort_days", effort_data)
            self.assertIn("expected_effort_hours", effort_data)

    def test_api_requires_authentication(self):
        """Test that API requires user to be logged in."""
        url = f"{settings.URL_PREFIX}/projects/api/project/{self.project.id}/status/"
        response = self.client.get(url)

        # Should redirect to login page
        self.assertEqual(response.status_code, HTTPStatus.FOUND)

    def test_api_restricts_access_to_organization_members(self):
        """Test that API restricts access to users in the same organization."""
        self.client.force_login(self.other_user)
        url = f"{settings.URL_PREFIX}/projects/api/project/{self.project.id}/status/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn(b"not found or access denied", response.content)

    def test_api_handles_nonexistent_project(self):
        """Test that API handles requests for non-existent projects."""
        self.client.force_login(self.user)
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        url = f"{settings.URL_PREFIX}/projects/api/project/{fake_uuid}/status/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn(b"not found or access denied", response.content)

    def test_api_handles_project_without_dates(self):
        """Test that API handles projects without start/end dates."""
        # Use the existing project and remove dates
        project_without_dates = self.project
        project_without_dates.start_date = None
        project_without_dates.target_date = None
        project_without_dates.allocated_staff_days = None
        project_without_dates.save()

        self.client.force_login(self.user)
        url = f"{settings.URL_PREFIX}/projects/api/project/{project_without_dates.id}/status/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json.loads(response.content)

        # Should have null dates
        self.assertIsNone(data["project"]["start_date"])
        self.assertIsNone(data["project"]["target_date"])

    def test_api_includes_efforts_outside_project_dates(self):
        """Test that API includes effort data even if it's outside project start/end dates."""
        # Add effort before project start
        before_start = datetime.date(2023, 12, 25)
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=before_start,
            hours=10,
            created_by=self.user,
            updated_by=self.user,
        )

        # Add effort after project end
        after_end = datetime.date(2024, 4, 8)
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=after_end,
            hours=15,
            created_by=self.user,
            updated_by=self.user,
        )

        self.client.force_login(self.user)
        url = f"{settings.URL_PREFIX}/projects/api/project/{self.project.id}/status/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = json.loads(response.content)

        # Should include all 5 weeks (3 original + 2 new)
        self.assertEqual(len(data["weekly_effort"]), 5)

        # Verify dates outside project range are included
        week_starts = [entry["week_start"] for entry in data["weekly_effort"]]
        self.assertIn("2023-12-25", week_starts)
        self.assertIn("2024-04-08", week_starts)

    def test_api_only_accepts_get_requests(self):
        """Test that API only accepts GET requests."""
        self.client.force_login(self.user)
        url = f"{settings.URL_PREFIX}/projects/api/project/{self.project.id}/status/"

        # POST should be rejected
        response = self.client.post(url)
        self.assertEqual(response.status_code, HTTPStatus.METHOD_NOT_ALLOWED)

        # PUT should be rejected
        response = self.client.put(url)
        self.assertEqual(response.status_code, HTTPStatus.METHOD_NOT_ALLOWED)

        # DELETE should be rejected
        response = self.client.delete(url)
        self.assertEqual(response.status_code, HTTPStatus.METHOD_NOT_ALLOWED)
