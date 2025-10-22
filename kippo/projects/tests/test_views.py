from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import Client, TestCase


class SetOrganizationTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        self.other_organization = KippoOrganization.objects.create(
            name="other-test-organization",
            github_organization_name="isstaffmodeladmintestcasebase-other-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # add membership
        membership = OrganizationMembership(
            user=self.user,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )
        membership.save()
        self.nonmember_organization = KippoOrganization.objects.create(
            name="nonmember-test-organization",
            github_organization_name="isstaffmodeladmintestcasebase-nonmember-testorg",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.no_org_user = KippoUser(
            username="noorguser",
            github_login="noorguser",
            password="test",  # noqa: S106
            email="noorguser@github.com",
            is_staff=True,
        )
        self.no_org_user.save()

        self.client = Client()

    def test_set_organization__valid_user(self):
        url = f"{settings.URL_PREFIX}/projects/set/organization/{self.organization.id}/"
        self.client.force_login(self.user)
        response = self.client.get(url)
        expected = HTTPStatus.FOUND
        actual = response.status_code
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")
        self.assertTrue(self.client.session["organization_id"] == str(self.organization.id))

    def test_set_organization__valid_user_nonmember_org(self):
        url = f"{settings.URL_PREFIX}/projects/set/organization/{self.nonmember_organization.id}/"
        self.client.force_login(self.user)
        response = self.client.get(url)
        expected = HTTPStatus.FOUND
        actual = response.status_code
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        actual = self.client.session["organization_id"]
        self.assertTrue(actual != str(self.nonmember_organization.id))
        self.assertTrue(actual == str(self.user.organizations[0].id))

    def test_set_organization__user_no_org(self):
        url = f"{settings.URL_PREFIX}/projects/set/organization/{self.nonmember_organization.id}/"
        self.client.force_login(self.no_org_user)
        response = self.client.get(url)
        expected = HTTPStatus.BAD_REQUEST
        actual = response.status_code
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        actual = self.client.session.get("organization_id", None)
        self.assertTrue(actual is None)


# class ProjectMilestonesTestCase(TestCase):
#     fixtures = DEFAULT_FIXTURES
#
#     def setUp(self):
#         created = setup_basic_project()
#         self.organization = created["KippoOrganization"]
#         self.user = created["KippoUser"]
#         self.project = created["KippoProject"]
#         self.task = created["KippoTask"]
#         self.github_manager = KippoUser.objects.get(username="github-manager")
#         self.other_organization = KippoOrganization.objects.create(
#             name="other-test-organization",
#             github_organization_name="isstaffmodeladmintestcasebase-other-testorg",
#             created_by=self.github_manager,
#             updated_by=self.github_manager,
#         )
#         # add membership
#         membership = OrganizationMembership(
#             user=self.user,
#             organization=self.other_organization,
#             created_by=self.github_manager,
#             updated_by=self.github_manager,
#             is_developer=True,
#         )
#         membership.save()
#         self.nonmember_organization = KippoOrganization.objects.create(
#             name="nonmember-test-organization",
#             github_organization_name="isstaffmodeladmintestcasebase-nonmember-testorg",
#             created_by=self.github_manager,
#             updated_by=self.github_manager,
#         )
#
#         self.no_org_user = KippoUser(
#             username="noorguser",
#             github_login="noorguser",
#             password="test",  # noqa: S106
#             email="noorguser@github.com",
#             is_staff=True,
#         )
#         self.no_org_user.save()
#         self.planning_column_name = "planning"
#         self.client = Client()
#
#         # set start_date, target_date for project
#         self.project.start_date = timezone.datetime(2020, 9, 1).date()
#         self.project.target_date = timezone.datetime(2020, 11, 1).date()
#         self.project.save()
#
#         milestone1_startdate = timezone.datetime(2020, 9, 1).date()
#         milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
#         self.kippomilestone_1 = KippoMilestone(
#             project=self.project,
#             title="test milestone 1",
#             is_completed=False,
#             start_date=milestone1_startdate,
#             target_date=milestone1_targetdate,
#         )
#         self.kippomilestone_1.save()
#         self.firstdate = timezone.datetime(2020, 9, 2).date()
#
#     def test_view_milestone_status__no_kippotaskstatus(self):
#         self.client.force_login(self.user)
#
#         url = reverse("view_milestone_status")
#         response = self.client.get(url)
#         self.assertEqual(response.status_code, HTTPStatus.OK)
#
#     def test_view_milestone_status__with_kippotaskstatus(self):
#         self.client.force_login(self.user)
#
#         url = reverse("view_milestone_status")
#         # create KippoTaskStatus object and confirm 200 is returned as expected
#         # create existing taskstatus
#         self.task1_status1 = KippoTaskStatus(
#             task=self.task,
#             state=self.planning_column_name,
#             effort_date=self.firstdate,
#             estimate_days=3,
#             created_by=self.github_manager,
#             updated_by=self.github_manager,
#         )
#         self.task1_status1.save()
#         response = self.client.get(url)
#         self.assertEqual(response.status_code, HTTPStatus.OK)
#
#     def test_view_milestone_status__with_milestone_id(self):
#         assert KippoMilestone.objects.filter(id=self.kippomilestone_1.id).exists()
#         self.client.force_login(self.user)
#         # create KippoTaskStatus object and confirm 200 is returned as expected
#         # create existing taskstatus
#         self.task1_status1 = KippoTaskStatus(
#             task=self.task,
#             state=self.planning_column_name,
#             effort_date=self.firstdate,
#             estimate_days=3,
#             created_by=self.github_manager,
#             updated_by=self.github_manager,
#         )
#         self.task1_status1.save()
#
#         assert self.kippomilestone_1.project.organization.id
#         session = self.client.session  # *MUST* pull out 'session' as variable to update
#         session.update({"organization_id": str(self.kippomilestone_1.project.organization.id)})
#         session.save()
#         url = reverse("view_milestone_status")
#         url = f"{url}{self.kippomilestone_1.id}/"
#         response = self.client.get(url)
#         self.assertEqual(response.status_code, HTTPStatus.OK, response.content)


class ProjectStatusDetailsTestCase(TestCase):
    """Test cases for the project status details view."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        import datetime

        from projects.models import ProjectWeeklyEffort

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
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=datetime.date(2024, 1, 1),
            hours=40,
            created_by=self.user,
            updated_by=self.user,
        )
        ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=datetime.date(2024, 1, 8),
            hours=35,
            created_by=self.user,
            updated_by=self.user,
        )

        self.client = Client()

    def test_view_returns_200_for_authorized_user(self):
        """Test that view returns 200 OK for authorized users."""
        self.client.force_login(self.user)
        url = f"{settings.URL_PREFIX}/projects/project/{self.project.id}/status/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_view_requires_authentication(self):
        """Test that view requires authentication."""
        url = f"{settings.URL_PREFIX}/projects/project/{self.project.id}/status/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.FOUND)

    def test_view_restricts_access_to_organization_members(self):
        """Test that view restricts access to organization members."""
        self.client.force_login(self.other_user)
        url = f"{settings.URL_PREFIX}/projects/project/{self.project.id}/status/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)

    def test_view_handles_nonexistent_project(self):
        """Test that view handles non-existent projects."""
        self.client.force_login(self.user)
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        url = f"{settings.URL_PREFIX}/projects/project/{fake_uuid}/status/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)

    def test_view_contains_chart_data(self):
        """Test that view context contains chart data."""
        import json

        self.client.force_login(self.user)
        url = f"{settings.URL_PREFIX}/projects/project/{self.project.id}/status/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

        # Check context data
        self.assertIn("chart_data_json", response.context)
        chart_data_json = response.context["chart_data_json"]
        chart_data = json.loads(chart_data_json)

        # Verify chart data structure
        self.assertIn("labels", chart_data)
        self.assertIn("users", chart_data)
        self.assertIn("effort_by_user", chart_data)
        self.assertIn("weekly_effort_by_user", chart_data)
        self.assertIn("expected_effort", chart_data)
        self.assertIn("start_date", chart_data)
        self.assertIn("target_date", chart_data)
        self.assertIn("allocated_hours", chart_data)

        # Verify we have data
        # Should include all Mondays from start_date (2024-01-01) to target_date (2024-03-31)
        # 2024-01-01 is a Monday, 2024-03-31 is a Sunday
        # Weeks: Jan 1, 8, 15, 22, 29, Feb 5, 12, 19, 26, Mar 4, 11, 18, 25
        # That's 13 weeks from start to last Monday before/on target_date
        self.assertGreaterEqual(len(chart_data["labels"]), 13)  # At least 13 weeks of dates
        self.assertTrue(len(chart_data["users"]) > 0)

        # Verify cumulative values
        user_display_name = self.user.display_name
        self.assertIn(user_display_name, chart_data["effort_by_user"])
        user_effort = chart_data["effort_by_user"][user_display_name]

        # Find the indices for the weeks with actual effort
        # Effort was recorded for 2024-01-01 and 2024-01-08
        labels = chart_data["labels"]
        idx_jan1 = labels.index("2024-01-01")
        idx_jan8 = labels.index("2024-01-08")

        # First week with effort should show 40h cumulative
        self.assertEqual(user_effort[idx_jan1], 40)
        # Second week with effort should show 75h cumulative (40 + 35)
        self.assertEqual(user_effort[idx_jan8], 75)
        # All subsequent weeks should remain at 75h cumulative
        if len(user_effort) > idx_jan8 + 1:
            self.assertEqual(user_effort[idx_jan8 + 1], 75)

        # Verify weekly values
        self.assertIn(user_display_name, chart_data["weekly_effort_by_user"])
        user_weekly_effort = chart_data["weekly_effort_by_user"][user_display_name]
        self.assertEqual(user_weekly_effort[idx_jan1], 40)  # First week: 40h
        self.assertEqual(user_weekly_effort[idx_jan8], 35)  # Second week: 35h (not cumulative)
        # Weeks without effort should show 0
        if len(user_weekly_effort) > idx_jan8 + 1:
            self.assertEqual(user_weekly_effort[idx_jan8 + 1], 0)

        # Verify project dates
        self.assertEqual(chart_data["start_date"], self.project.start_date.isoformat())
        self.assertEqual(chart_data["target_date"], self.project.target_date.isoformat())

        # Verify allocated hours
        self.assertEqual(chart_data["allocated_hours"], self.project.allocated_effort_hours)

        # Verify project_progress_status is in context
        self.assertIn("project_progress_status", response.context)
        project_progress_status = response.context["project_progress_status"]
        self.assertIsNotNone(project_progress_status)

        # Verify pie chart data
        self.assertIn("pie_chart", chart_data)
        pie_data = chart_data["pie_chart"]
        self.assertIn("users", pie_data)
        self.assertIn("effort", pie_data)
        self.assertEqual(len(pie_data["users"]), len(pie_data["effort"]))
        # Verify the latest cumulative effort is included
        user_index = pie_data["users"].index(user_display_name)
        self.assertEqual(pie_data["effort"][user_index], 75)  # Latest cumulative: 40 + 35

    def test_view_renders_correct_template(self):
        """Test that view renders the correct template."""
        self.client.force_login(self.user)
        url = f"{settings.URL_PREFIX}/projects/project/{self.project.id}/status/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "projects/projectstatus_details.html")
