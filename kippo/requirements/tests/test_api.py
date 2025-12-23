"""Tests for the requirements REST API endpoints."""

import datetime
from http import HTTPStatus

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ..models import (
    ProjectAssumption,
    ProjectBusinessRequirement,
    ProjectBusinessRequirementCategory,
    ProjectBusinessRequirementComment,
    ProjectBusinessRequirementEstimate,
    ProjectProblemDefinition,
    ProjectTechnicalRequirement,
    ProjectTechnicalRequirementCategory,
    ProjectTechnicalRequirementGithubIssue,
)

API_PREFIX = f"{settings.URL_PREFIX}/api/requirements"


class RequirementsAPIEndpointsTestCase(TestCase):
    """Test that all requirements API endpoints are available at /api/requirements/."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_problem_definitions_endpoint(self):
        """Test /api/requirements/problem-definitions/ endpoint is accessible."""
        url = f"{API_PREFIX}/problem-definitions/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_assumptions_endpoint(self):
        """Test /api/requirements/assumptions/ endpoint is accessible."""
        url = f"{API_PREFIX}/assumptions/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_assumptions_categories_endpoint(self):
        """Test /api/requirements/assumptions/categories/ endpoint is accessible."""
        url = f"{API_PREFIX}/assumptions/categories/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_business_requirement_categories_endpoint(self):
        """Test /api/requirements/business-requirement-categories/ endpoint is accessible."""
        url = f"{API_PREFIX}/business-requirement-categories/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_technical_requirement_categories_endpoint(self):
        """Test /api/requirements/technical-requirement-categories/ endpoint is accessible."""
        url = f"{API_PREFIX}/technical-requirement-categories/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_business_requirements_endpoint(self):
        """Test /api/requirements/business-requirements/ endpoint is accessible."""
        url = f"{API_PREFIX}/business-requirements/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_technical_requirements_endpoint(self):
        """Test /api/requirements/technical-requirements/ endpoint is accessible."""
        url = f"{API_PREFIX}/technical-requirements/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_business_requirement_comments_endpoint(self):
        """Test /api/requirements/business-requirement-comments/ endpoint is accessible."""
        url = f"{API_PREFIX}/business-requirement-comments/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_technical_requirement_comments_endpoint(self):
        """Test /api/requirements/technical-requirement-comments/ endpoint is accessible."""
        url = f"{API_PREFIX}/technical-requirement-comments/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_estimates_endpoint(self):
        """Test /api/requirements/estimates/ endpoint is accessible."""
        url = f"{API_PREFIX}/estimates/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_github_issues_endpoint(self):
        """Test /api/requirements/github-issues/ endpoint is accessible."""
        url = f"{API_PREFIX}/github-issues/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)


class RequirementsAPIAuthenticationTestCase(TestCase):
    """Test that requirements API endpoints require authentication."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()

    def test_problem_definitions_requires_auth(self):
        """Test /api/requirements/problem-definitions/ requires authentication."""
        url = f"{API_PREFIX}/problem-definitions/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_assumptions_requires_auth(self):
        """Test /api/requirements/assumptions/ requires authentication."""
        url = f"{API_PREFIX}/assumptions/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_business_requirements_requires_auth(self):
        """Test /api/requirements/business-requirements/ requires authentication."""
        url = f"{API_PREFIX}/business-requirements/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_technical_requirements_requires_auth(self):
        """Test /api/requirements/technical-requirements/ requires authentication."""
        url = f"{API_PREFIX}/technical-requirements/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)


class ProblemDefinitionViewSetTestCase(TestCase):
    """Test cases for ProjectProblemDefinition REST API viewset."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        self.problem = ProjectProblemDefinition.objects.create(
            project=self.project,
            title="Test Problem",
            details="Problem details",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_problem_definitions(self):
        """Test listing problem definitions."""
        url = f"{API_PREFIX}/problem-definitions/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("results", data)
        self.assertEqual(data["count"], 1)

    def test_retrieve_problem_definition(self):
        """Test retrieving a single problem definition."""
        url = f"{API_PREFIX}/problem-definitions/{self.problem.id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["title"], "Test Problem")

    def test_create_problem_definition(self):
        """Test creating a problem definition."""
        url = f"{API_PREFIX}/problem-definitions/"
        data = {
            "project": str(self.project.id),
            "title": "New Problem",
            "details": "New problem details",
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(ProjectProblemDefinition.objects.count(), 2)

    def test_update_problem_definition(self):
        """Test updating a problem definition."""
        url = f"{API_PREFIX}/problem-definitions/{self.problem.id}/"
        data = {"title": "Updated Problem"}
        response = self.client.patch(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.title, "Updated Problem")

    def test_delete_problem_definition(self):
        """Test deleting a problem definition."""
        url = f"{API_PREFIX}/problem-definitions/{self.problem.id}/"
        response = self.client.delete(url)

        self.assertEqual(response.status_code, HTTPStatus.NO_CONTENT)
        self.assertEqual(ProjectProblemDefinition.objects.count(), 0)

    def test_filter_by_project(self):
        """Test filtering problem definitions by project."""
        url = f"{API_PREFIX}/problem-definitions/?project={self.project.id}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)


class AssumptionViewSetTestCase(TestCase):
    """Test cases for ProjectAssumption REST API viewset."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        self.assumption = ProjectAssumption.objects.create(
            project=self.project,
            category="assumption",
            title="Test Assumption",
            details="Assumption details",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_assumptions(self):
        """Test listing assumptions."""
        url = f"{API_PREFIX}/assumptions/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)

    def test_create_assumption(self):
        """Test creating an assumption."""
        url = f"{API_PREFIX}/assumptions/"
        data = {
            "project": str(self.project.id),
            "category": "constraint",
            "title": "New Constraint",
            "details": "Constraint details",
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(ProjectAssumption.objects.count(), 2)

    def test_filter_by_category(self):
        """Test filtering assumptions by category."""
        ProjectAssumption.objects.create(
            project=self.project,
            category="constraint",
            title="Test Constraint",
        )

        url = f"{API_PREFIX}/assumptions/?category=assumption"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)

    def test_categories_action(self):
        """Test the categories action returns valid choices."""
        url = f"{API_PREFIX}/assumptions/categories/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertTrue(len(data) > 0)
        self.assertIn("value", data[0])
        self.assertIn("label", data[0])


class BusinessRequirementViewSetTestCase(TestCase):
    """Test cases for ProjectBusinessRequirement REST API viewset."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        self.category = ProjectBusinessRequirementCategory.objects.create(
            project=self.project,
            name="Test Category",
        )

        self.requirement = ProjectBusinessRequirement.objects.create(
            project=self.project,
            category=self.category,
            title="Test Requirement",
            details="Requirement details",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_business_requirements(self):
        """Test listing business requirements."""
        url = f"{API_PREFIX}/business-requirements/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)

    def test_retrieve_business_requirement(self):
        """Test retrieving a single business requirement."""
        url = f"{API_PREFIX}/business-requirements/{self.requirement.id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["title"], "Test Requirement")

    def test_create_business_requirement(self):
        """Test creating a business requirement."""
        url = f"{API_PREFIX}/business-requirements/"
        data = {
            "project": str(self.project.id),
            "category": self.category.id,
            "title": "New Requirement",
            "details": "New requirement details",
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(ProjectBusinessRequirement.objects.count(), 2)

    def test_filter_by_category(self):
        """Test filtering business requirements by category."""
        url = f"{API_PREFIX}/business-requirements/?category={self.category.id}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)


class TechnicalRequirementViewSetTestCase(TestCase):
    """Test cases for ProjectTechnicalRequirement REST API viewset."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        self.category = ProjectTechnicalRequirementCategory.objects.create(
            project=self.project,
            name="Test Tech Category",
        )

        self.requirement = ProjectTechnicalRequirement.objects.create(
            project=self.project,
            category=self.category,
            title="Test Technical Requirement",
            details="Technical requirement details",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_technical_requirements(self):
        """Test listing technical requirements."""
        url = f"{API_PREFIX}/technical-requirements/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)

    def test_retrieve_technical_requirement(self):
        """Test retrieving a single technical requirement."""
        url = f"{API_PREFIX}/technical-requirements/{self.requirement.id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["title"], "Test Technical Requirement")

    def test_create_technical_requirement(self):
        """Test creating a technical requirement."""
        url = f"{API_PREFIX}/technical-requirements/"
        data = {
            "project": str(self.project.id),
            "category": self.category.id,
            "title": "New Tech Requirement",
            "details": "New tech requirement details",
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(ProjectTechnicalRequirement.objects.count(), 2)


class BusinessRequirementCommentViewSetTestCase(TestCase):
    """Test cases for ProjectBusinessRequirementComment REST API viewset."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        self.category = ProjectBusinessRequirementCategory.objects.create(
            project=self.project,
            name="Test Category",
        )

        self.requirement = ProjectBusinessRequirement.objects.create(
            project=self.project,
            category=self.category,
            title="Test Requirement",
        )

        self.comment = ProjectBusinessRequirementComment.objects.create(
            requirement=self.requirement,
            comment="Test comment",
            created_by=self.user,
            updated_by=self.user,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_comments(self):
        """Test listing business requirement comments."""
        url = f"{API_PREFIX}/business-requirement-comments/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)

    def test_create_comment(self):
        """Test creating a comment."""
        url = f"{API_PREFIX}/business-requirement-comments/"
        data = {
            "requirement": self.requirement.id,
            "comment": "New comment",
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(ProjectBusinessRequirementComment.objects.count(), 2)

    def test_toggle_resolved(self):
        """Test toggling the resolved status of a comment."""
        url = f"{API_PREFIX}/business-requirement-comments/{self.comment.id}/toggle_resolved/"
        response = self.client.post(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.is_resolved)

        # Toggle back
        response = self.client.post(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.comment.refresh_from_db()
        self.assertFalse(self.comment.is_resolved)

    def test_filter_by_requirement(self):
        """Test filtering comments by requirement."""
        url = f"{API_PREFIX}/business-requirement-comments/?requirement={self.requirement.id}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)


class EstimateViewSetTestCase(TestCase):
    """Test cases for ProjectBusinessRequirementEstimate REST API viewset."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        self.tech_category = ProjectTechnicalRequirementCategory.objects.create(
            project=self.project,
            name="Test Tech Category",
        )

        self.tech_requirement = ProjectTechnicalRequirement.objects.create(
            project=self.project,
            category=self.tech_category,
            title="Test Tech Requirement",
        )

        self.estimate = ProjectBusinessRequirementEstimate.objects.create(
            requirement=self.tech_requirement,
            days=5.0,
            confidence=0.8,
            created_by=self.user,
            updated_by=self.user,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_estimates(self):
        """Test listing estimates."""
        url = f"{API_PREFIX}/estimates/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)

    def test_retrieve_estimate(self):
        """Test retrieving a single estimate."""
        url = f"{API_PREFIX}/estimates/{self.estimate.id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["days"], 5.0)
        self.assertEqual(data["confidence"], 0.8)

    def test_filter_by_project(self):
        """Test filtering estimates by project."""
        url = f"{API_PREFIX}/estimates/?project={self.project.id}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)


class GithubIssueViewSetTestCase(TestCase):
    """Test cases for ProjectTechnicalRequirementGithubIssue REST API viewset."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        self.tech_category = ProjectTechnicalRequirementCategory.objects.create(
            project=self.project,
            name="Test Tech Category",
        )

        self.tech_requirement = ProjectTechnicalRequirement.objects.create(
            project=self.project,
            category=self.tech_category,
            title="Test Tech Requirement",
        )

        self.github_issue = ProjectTechnicalRequirementGithubIssue.objects.create(
            technical_requirement=self.tech_requirement,
            url="https://github.com/org/repo/issues/1",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_github_issues(self):
        """Test listing github issues."""
        url = f"{API_PREFIX}/github-issues/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)

    def test_create_github_issue(self):
        """Test creating a github issue link."""
        url = f"{API_PREFIX}/github-issues/"
        data = {
            "technical_requirement": self.tech_requirement.id,
            "url": "https://github.com/org/repo/issues/2",
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(ProjectTechnicalRequirementGithubIssue.objects.count(), 2)

    def test_filter_by_technical_requirement(self):
        """Test filtering github issues by technical requirement."""
        url = f"{API_PREFIX}/github-issues/?technical_requirement={self.tech_requirement.id}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)


class OrganizationScopingTestCase(TestCase):
    """Test that requirements API endpoints are properly scoped by organization."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Create problem definition in user's org
        self.problem = ProjectProblemDefinition.objects.create(
            project=self.project,
            title="User's Problem",
        )

        # Create another organization and project
        from accounts.models import KippoOrganization

        self.other_org = KippoOrganization.objects.create(
            name="other-org",
            github_organization_name="other-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        from projects.models import KippoProject

        self.other_project = KippoProject.objects.create(
            name="Other Project",
            organization=self.other_org,
            columnset=self.project.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # Create problem definition in other org
        self.other_problem = ProjectProblemDefinition.objects.create(
            project=self.other_project,
            title="Other's Problem",
        )

        # Create user in other org
        self.other_user = KippoUser.objects.create(
            username="otheruser",
            github_login="otheruser",
            email="otheruser@example.com",
            is_staff=True,
        )
        OrganizationMembership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )

        self.client = APIClient()

    def test_user_sees_only_own_organization_data(self):
        """Test that regular users only see data from their organizations."""
        self.client.force_authenticate(user=self.user)
        url = f"{API_PREFIX}/problem-definitions/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["title"], "User's Problem")

    def test_user_cannot_access_other_organization_data(self):
        """Test that regular users cannot access data from other organizations."""
        self.client.force_authenticate(user=self.user)
        url = f"{API_PREFIX}/problem-definitions/{self.other_problem.id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_superuser_sees_all_data(self):
        """Test that superusers can see data from all organizations."""
        superuser = KippoUser.objects.create(
            username="superuser",
            github_login="superuser",
            email="superuser@example.com",
            is_staff=True,
            is_superuser=True,
        )

        self.client.force_authenticate(user=superuser)
        url = f"{API_PREFIX}/problem-definitions/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], 2)


class ScheduleEstimationAPITestCase(TestCase):
    """Test cases for the schedule estimation API endpoint."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        # Set project dates
        self.project.start_date = timezone.localdate()
        self.project.target_date = timezone.localdate() + datetime.timedelta(days=90)
        self.project.save()

        # Create technical requirement category
        self.tech_category = ProjectTechnicalRequirementCategory.objects.create(
            project=self.project,
            name="Backend",
        )

        # Create technical requirements with estimates
        self.tech_req1 = ProjectTechnicalRequirement.objects.create(
            project=self.project,
            category=self.tech_category,
            title="Implement user authentication",
            details="Add JWT auth",
        )
        self.estimate1 = ProjectBusinessRequirementEstimate.objects.create(
            requirement=self.tech_req1,
            days=5.0,
            confidence=0.8,
            created_by=self.user,
            updated_by=self.user,
        )

        self.tech_req2 = ProjectTechnicalRequirement.objects.create(
            project=self.project,
            category=self.tech_category,
            title="Create API endpoints",
            details="REST API for resources",
        )
        self.estimate2 = ProjectBusinessRequirementEstimate.objects.create(
            requirement=self.tech_req2,
            days=3.0,
            confidence=0.9,
            created_by=self.user,
            updated_by=self.user,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_schedule_estimation_endpoint_accessible(self):
        """Test that the schedule estimation endpoint is accessible."""
        url = f"{API_PREFIX}/schedule-estimation/"
        data = {
            "project": str(self.project.id),
            "developer_count": 1,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_schedule_estimation_returns_completion_date(self):
        """Test that schedule estimation returns an estimated completion date."""
        url = f"{API_PREFIX}/schedule-estimation/"
        data = {
            "project": str(self.project.id),
            "developer_count": 1,
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        result = response.json()
        self.assertIn("estimated_completion_date", result)
        self.assertIn("total_estimate_days", result)
        self.assertIn("requirements_count", result)
        self.assertEqual(result["requirements_count"], 2)
        self.assertEqual(result["total_estimate_days"], 8.0)  # 5 + 3

    def test_schedule_estimation_with_multiple_developers(self):
        """Test that more developers reduce completion time."""
        url = f"{API_PREFIX}/schedule-estimation/"

        # Schedule with 1 developer
        data_1_dev = {
            "project": str(self.project.id),
            "developer_count": 1,
        }
        response_1 = self.client.post(url, data_1_dev, format="json")
        result_1 = response_1.json()

        # Schedule with 2 developers
        data_2_dev = {
            "project": str(self.project.id),
            "developer_count": 2,
        }
        response_2 = self.client.post(url, data_2_dev, format="json")
        result_2 = response_2.json()

        # With more developers, completion date should be earlier or equal
        date_1 = datetime.date.fromisoformat(result_1["estimated_completion_date"])
        date_2 = datetime.date.fromisoformat(result_2["estimated_completion_date"])
        self.assertLessEqual(date_2, date_1)

    def test_schedule_estimation_with_custom_start_date(self):
        """Test schedule estimation with a custom start date."""
        url = f"{API_PREFIX}/schedule-estimation/"
        future_date = (timezone.localdate() + datetime.timedelta(days=7)).isoformat()
        data = {
            "project": str(self.project.id),
            "developer_count": 1,
            "start_date": future_date,
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        result = response.json()
        self.assertEqual(result["schedule_start_date"], future_date)

    def test_schedule_estimation_requires_authentication(self):
        """Test that schedule estimation requires authentication."""
        self.client.logout()
        unauthenticated_client = APIClient()
        url = f"{API_PREFIX}/schedule-estimation/"
        data = {
            "project": str(self.project.id),
            "developer_count": 1,
        }
        response = unauthenticated_client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_schedule_estimation_validates_developer_count(self):
        """Test that developer count must be positive."""
        url = f"{API_PREFIX}/schedule-estimation/"
        data = {
            "project": str(self.project.id),
            "developer_count": 0,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)

    def test_schedule_estimation_project_not_found(self):
        """Test error when project doesn't exist."""
        url = f"{API_PREFIX}/schedule-estimation/"
        data = {
            "project": "00000000-0000-0000-0000-000000000000",
            "developer_count": 1,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_schedule_estimation_no_technical_requirements(self):
        """Test error when project has no technical requirements."""
        # Delete all technical requirements
        ProjectTechnicalRequirement.objects.filter(project=self.project).delete()

        url = f"{API_PREFIX}/schedule-estimation/"
        data = {
            "project": str(self.project.id),
            "developer_count": 1,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("error", response.json())

    def test_schedule_estimation_no_estimates(self):
        """Test error when technical requirements have no estimates."""
        # Delete all estimates
        ProjectBusinessRequirementEstimate.objects.filter(requirement__project=self.project).delete()

        url = f"{API_PREFIX}/schedule-estimation/"
        data = {
            "project": str(self.project.id),
            "developer_count": 1,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("error", response.json())

    def test_schedule_estimation_returns_scheduled_requirements(self):
        """Test that response includes details of each scheduled requirement."""
        url = f"{API_PREFIX}/schedule-estimation/"
        data = {
            "project": str(self.project.id),
            "developer_count": 1,
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        result = response.json()
        self.assertIn("scheduled_requirements", result)
        self.assertEqual(len(result["scheduled_requirements"]), 2)

        req = result["scheduled_requirements"][0]
        self.assertIn("id", req)
        self.assertIn("display_id", req)
        self.assertIn("title", req)
        self.assertIn("estimate_days", req)
        self.assertIn("confidence", req)
        self.assertIn("scheduled_start_date", req)
        self.assertIn("scheduled_end_date", req)

    def test_schedule_estimation_start_date_in_past_rejected(self):
        """Test that start_date in the past is rejected."""
        url = f"{API_PREFIX}/schedule-estimation/"
        past_date = (timezone.localdate() - datetime.timedelta(days=1)).isoformat()
        data = {
            "project": str(self.project.id),
            "developer_count": 1,
            "start_date": past_date,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
