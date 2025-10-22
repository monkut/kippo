"""Tests for the KippoProjectAdmin get_projectstatus_display method."""

import datetime

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase

from ..admin import KippoProjectAdmin
from ..models import KippoProject, ProjectWeeklyEffort


class GetProjectStatusDisplayTestCase(TestCase):
    """Test cases for the get_projectstatus_display admin method."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        # Set project dates and allocated staff days
        self.project.start_date = datetime.date(2024, 1, 1)
        self.project.target_date = datetime.date(2024, 3, 31)
        self.project.allocated_staff_days = 60
        self.project.save()

        # Create ProjectWeeklyEffort entries
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=datetime.date(2024, 1, 1),
            hours=40,
            created_by=self.user,
            updated_by=self.user,
        )

        self.admin = KippoProjectAdmin(model=KippoProject, admin_site=None)

    def test_display_contains_link(self):
        """Test that the display contains a link to the project status details page."""
        result = self.admin.get_projectstatus_display(self.project)

        # Check that result contains the link
        expected_url = f"{settings.URL_PREFIX}/projects/project/{self.project.id}/status/"
        self.assertIn(f'href="{expected_url}"', result)
        self.assertIn("<a ", result)
        self.assertIn("</a>", result)

    def test_display_contains_effort_info(self):
        """Test that the display still contains the effort information."""
        result = self.admin.get_projectstatus_display(self.project)

        # Should contain hours information
        self.assertIn("40h", result)

    def test_display_without_object(self):
        """Test that display returns dash when no object is provided."""
        result = self.admin.get_projectstatus_display(None)
        self.assertEqual(result, "-")

    def test_link_wraps_entire_display(self):
        """Test that the link wraps the entire status display including meter."""
        result = self.admin.get_projectstatus_display(self.project)

        # Link should come before the content
        link_start = result.find("<a ")
        link_end = result.find("</a>")

        # Verify link exists
        self.assertGreater(link_start, -1, "Link start tag not found")
        self.assertGreater(link_end, -1, "Link end tag not found")

        # Verify link comes before closing tag
        self.assertLess(link_start, link_end, "Link structure is malformed")
