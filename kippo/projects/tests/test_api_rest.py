"""Tests for the projects REST API viewsets."""

import datetime
from http import HTTPStatus
from unittest import mock

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from drf_spectacular.generators import SchemaGenerator
from freezegun import freeze_time
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from ..models import PHASE_CONFIDENCE, KippoProject, KippoProjectContract, KippoProjectOrganizationCategory, ProjectColumnSet, ProjectWeeklyEffort


def _registration_fields(organization: KippoOrganization, project_manager: KippoUser) -> dict:
    """Required-at-registration project fields for POST /api/projects/ (kippo#40 / T19)."""
    from customers.models import KippoCustomer

    customer = KippoCustomer.objects.create(
        organization=organization,
        name="reg-test-customer",
        created_by=project_manager,
        updated_by=project_manager,
    )
    return {
        "customer": str(customer.id),
        "project_manager": project_manager.id,
        "start_date": "2026-01-01",
        "target_date": "2026-03-31",
    }


class JWTAuthenticationTestCase(TestCase):
    """Test cases for JWT authentication endpoints."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.client = APIClient()

    def test_obtain_jwt_token(self):
        """Test obtaining JWT token pair."""
        # Set password for user
        self.user.set_password("testpassword123")
        self.user.save()

        url = f"{settings.URL_PREFIX}/api/token/"
        response = self.client.post(
            url,
            {"username": self.user.username, "password": "testpassword123"},
            format="json",
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("access", data)
        self.assertIn("refresh", data)

    def test_refresh_jwt_token(self):
        """Test refreshing JWT token."""
        refresh = RefreshToken.for_user(self.user)
        url = f"{settings.URL_PREFIX}/api/token/refresh/"
        response = self.client.post(url, {"refresh": str(refresh)}, format="json")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("access", data)

    def test_api_requires_authentication(self):
        """Test that API endpoints require authentication."""
        url = f"{settings.URL_PREFIX}/api/projects/"
        response = self.client.get(url)
        # DRF returns 401 UNAUTHORIZED when no authentication is provided
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)


class KippoProjectViewSetTestCase(TestCase):
    """Test cases for KippoProject REST API viewset."""

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

        # Create another project in a different organization
        self.other_project = KippoProject.objects.create(
            name="Other Project",
            organization=self.other_organization,
            columnset=self.project.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_projects(self):
        """Test listing projects."""
        url = f"{settings.URL_PREFIX}/api/projects/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("results", data)
        # User should only see projects from their organization
        project_ids = [result["id"] for result in data["results"]]
        self.assertIn(str(self.project.id), project_ids)
        # Should not see projects from other organizations
        self.assertNotIn(str(self.other_project.id), project_ids)

    def test_retrieve_project(self):
        """Test retrieving a single project."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["id"], str(self.project.id))
        self.assertEqual(data["name"], self.project.name)
        self.assertEqual(data["organization_name"], self.organization.name)
        self.assertEqual(data["allocated_staff_days"], 60)
        self.assertEqual(data["allocated_effort_hours"], 60 * settings.DAY_WORKHOURS)
        # phase_display exposes the human-readable status label for the phase key (kippo#37 / T10)
        self.assertEqual(data["phase_display"], self.project.get_phase_display())

    def test_retrieve_project_exposes_derived_revenue_figures(self):
        """total_revenue (ledger) + contract_amount (contracts) are read-only derived fields (kippo#32 / T13)."""
        from decimal import Decimal

        # monthly contract over the project period (2024-01-01 .. 2024-03-31) -> 3 month-end entries
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type="monthly",
            total_amount=Decimal("900000"),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        ).generate_billing_entries(created_by=self.github_manager)

        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        data = self.client.get(url).json()
        # contract_amount = total_amount; total_revenue = ledger sum (900,000 split across 3 months)
        self.assertEqual(data["contract_amount"], "900000")
        self.assertEqual(data["total_revenue"], "900000")

    def test_derived_revenue_figures_are_read_only(self):
        """Writes to total_revenue / contract_amount are ignored (read-only)."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.patch(url, {"total_revenue": "999", "contract_amount": "888"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        # no contracts/entries -> derived values stay 0, not the posted values
        self.assertEqual(data["total_revenue"], "0")
        self.assertEqual(data["contract_amount"], "0")

    def test_confidence_is_writable_via_api(self):
        """Confidence can be set directly for manual override; a phase-unchanged update persists it."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        original_phase = self.project.phase
        response = self.client.patch(url, {"confidence": 55}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.json()["confidence"], 55)
        self.project.refresh_from_db()
        self.assertEqual(self.project.confidence, 55)  # set value sticks (save() does not re-derive)
        self.assertEqual(self.project.phase, original_phase)  # phase untouched

    def test_confidence_out_of_range_rejected(self):
        """Confidence is validated to 0-100."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.patch(url, {"confidence": 150}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)

    def test_confidence_follows_phase_when_both_changed(self):
        """When an update also changes phase, confidence is re-derived from the new phase and any
        sent confidence is ignored (documented last-writer behavior).
        """
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.patch(url, {"phase": "under-contract", "confidence": 42}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.json()["confidence"], PHASE_CONFIDENCE["under-contract"])  # 100, not 42
        self.project.refresh_from_db()
        self.assertEqual(self.project.confidence, PHASE_CONFIDENCE["under-contract"])
        self.assertEqual(self.project.phase, "under-contract")

    def test_list_exposes_category_label_and_billing_types(self):
        """List/detail expose category_label + the contract billing_type (kippo#39 / T14)."""
        from decimal import Decimal

        # one contract per project (OneToOne) -> billing_types is its one-element list
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type="monthly",
            total_amount=Decimal("900000"),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        list_data = self.client.get(f"{settings.URL_PREFIX}/api/projects/").json()
        row = next(r for r in list_data["results"] if r["id"] == str(self.project.id))
        self.assertEqual(row["category_label"], self.project.category.label)
        self.assertEqual(row["billing_types"], ["monthly"])

    def test_monthly_billing_schedule_exposed_for_per_month_rows(self):
        """Monthly projects expose a per-month schedule for the list's per-month rows (kippo#39 / T15)."""
        from decimal import Decimal

        KippoProjectContract.objects.create(
            project=self.project,
            billing_type="monthly",
            total_amount=Decimal("900000"),  # 900,000 over 3 months -> 300,000 each
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 3, 31),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        schedule = self.client.get(url).json()["monthly_billing_schedule"]
        self.assertEqual(
            schedule,
            [
                {"month": "2026-01-31", "amount": "300000"},
                {"month": "2026-02-28", "amount": "300000"},
                {"month": "2026-03-31", "amount": "300000"},
            ],
        )

    def test_monthly_billing_schedule_empty_without_monthly_contract(self):
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        self.assertEqual(self.client.get(url).json()["monthly_billing_schedule"], [])

    def test_billing_types_empty_without_contracts(self):
        """billing_types is an empty list when the project has no contracts (kippo#39 / T14)."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        self.assertEqual(self.client.get(url).json()["billing_types"], [])

    def test_problem_definition_shown_in_list_and_detail(self):
        """problem_definition (reused as the project intro) is writable and shown in list/detail (kippo#29 / T07)."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        patch = self.client.patch(url, {"problem_definition": "AI-driven demand forecasting for retail."}, format="json")
        self.assertEqual(patch.status_code, HTTPStatus.OK)
        self.assertEqual(patch.json()["problem_definition"], "AI-driven demand forecasting for retail.")
        # surfaced in detail and list
        self.assertEqual(self.client.get(url).json()["problem_definition"], "AI-driven demand forecasting for retail.")
        list_data = self.client.get(f"{settings.URL_PREFIX}/api/projects/").json()
        project_row = next(r for r in list_data["results"] if r["id"] == str(self.project.id))
        self.assertEqual(project_row["problem_definition"], "AI-driven demand forecasting for retail.")

    def test_customer_link_and_contract_folder_url(self):
        """Project create/edit can set the customer; the contract-folder URL (customer.document_url)
        is exposed read-only on the project (kippo#34 / T04).
        """
        from customers.models import KippoCustomer

        customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="Acme Co",
            document_url="https://drive.example.com/acme/contracts",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        # link the customer via the project edit API
        patch = self.client.patch(url, {"customer": str(customer.id)}, format="json")
        self.assertEqual(patch.status_code, HTTPStatus.OK)
        data = patch.json()
        self.assertEqual(data["customer"], str(customer.id))
        self.assertEqual(data["customer_name"], "Acme Co")
        # contract-folder URL surfaces from the linked customer
        self.assertEqual(data["customer_document_url"], "https://drive.example.com/acme/contracts")

    def test_customer_document_url_null_without_customer(self):
        """customer_document_url is null when the project has no customer (kippo#34 / T04)."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        data = self.client.get(url).json()
        self.assertIsNone(data["customer_document_url"])

    def test_contract_folder_url_is_read_only_on_project(self):
        """Writing customer_document_url through the project is ignored — edit via the customer (kippo#34 / T04)."""
        from customers.models import KippoCustomer

        customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="Beta Co",
            document_url="https://drive.example.com/beta",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.project.customer = customer
        self.project.save()

        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.patch(url, {"customer_document_url": "https://evil.example.com"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        customer.refresh_from_db()
        self.assertEqual(customer.document_url, "https://drive.example.com/beta")  # unchanged

    def test_filter_by_is_active(self):
        """Test filtering projects by is_active parameter.

        The is_active=true filter should match ActiveKippoProject behavior:
        - display_as_active=True AND is_closed=False
        """
        # Create an inactive project (display_as_active=False)
        inactive_project = KippoProject.objects.create(
            name="Inactive Project",
            organization=self.organization,
            columnset=self.project.columnset,
            display_as_active=False,
            created_by=self.user,
            updated_by=self.user,
        )

        # Create a closed project (is_closed=True but display_as_active=True)
        closed_project = KippoProject.objects.create(
            name="Closed Project",
            organization=self.organization,
            columnset=self.project.columnset,
            display_as_active=True,
            is_closed=True,
            created_by=self.user,
            updated_by=self.user,
        )

        # Filter for active projects - should exclude both inactive and closed projects
        url = f"{settings.URL_PREFIX}/api/projects/?is_active=true"
        response = self.client.get(url)
        data = response.json()
        active_ids = [result["id"] for result in data["results"]]
        self.assertIn(str(self.project.id), active_ids)
        self.assertNotIn(str(inactive_project.id), active_ids)
        self.assertNotIn(str(closed_project.id), active_ids)

        # Filter for inactive projects
        url = f"{settings.URL_PREFIX}/api/projects/?is_active=false"
        response = self.client.get(url)
        data = response.json()
        inactive_ids = [result["id"] for result in data["results"]]
        self.assertIn(str(inactive_project.id), inactive_ids)
        self.assertNotIn(str(self.project.id), inactive_ids)

    def test_filter_by_category(self):
        """Test filtering projects by the category query parameter (exact match on the category key)."""
        si_category = KippoProjectOrganizationCategory.objects.get(organization__isnull=True, key="si")
        ai_category = KippoProjectOrganizationCategory.objects.get(organization__isnull=True, key="ai-development")
        self.project.category = si_category
        self.project.save()

        other_category_project = KippoProject.objects.create(
            name="AI Development Project",
            organization=self.organization,
            columnset=self.project.columnset,
            category=ai_category,
            created_by=self.user,
            updated_by=self.user,
        )

        # Filter to si — only the si project should be returned.
        url = f"{settings.URL_PREFIX}/api/projects/?category=si"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        category_ids = [result["id"] for result in response.json()["results"]]
        self.assertIn(str(self.project.id), category_ids)
        self.assertNotIn(str(other_category_project.id), category_ids)

        # Without the filter, both projects are returned.
        url = f"{settings.URL_PREFIX}/api/projects/"
        response = self.client.get(url)
        all_ids = [result["id"] for result in response.json()["results"]]
        self.assertIn(str(self.project.id), all_ids)
        self.assertIn(str(other_category_project.id), all_ids)

    def test_exclude_by_category(self):
        """Test excluding projects by the exclude_category query parameter (exact match on the category key)."""
        si_category = KippoProjectOrganizationCategory.objects.get(organization__isnull=True, key="si")
        non_project_category = KippoProjectOrganizationCategory.objects.get(organization__isnull=True, key="non-project")
        self.project.category = si_category
        self.project.save()

        non_project = KippoProject.objects.create(
            name="Non Project",
            organization=self.organization,
            columnset=self.project.columnset,
            category=non_project_category,
            created_by=self.user,
            updated_by=self.user,
        )

        # Exclude non-project — only the si project should be returned.
        url = f"{settings.URL_PREFIX}/api/projects/?exclude_category=non-project"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        result_ids = [result["id"] for result in response.json()["results"]]
        self.assertIn(str(self.project.id), result_ids)
        self.assertNotIn(str(non_project.id), result_ids)

        # Without the filter, both projects are returned.
        url = f"{settings.URL_PREFIX}/api/projects/"
        response = self.client.get(url)
        all_ids = [result["id"] for result in response.json()["results"]]
        self.assertIn(str(self.project.id), all_ids)
        self.assertIn(str(non_project.id), all_ids)

    def test_search_filters_by_name_substring(self):
        """Test the `search` query parameter filters projects by case-insensitive name substring."""
        match = KippoProject.objects.create(
            name="Demand Forecasting Platform",
            organization=self.organization,
            columnset=self.project.columnset,
            created_by=self.user,
            updated_by=self.user,
        )
        url = f"{settings.URL_PREFIX}/api/projects/?search=forecasting"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        result_ids = [result["id"] for result in response.json()["results"]]
        self.assertIn(str(match.id), result_ids)
        self.assertNotIn(str(self.project.id), result_ids)

    def test_parent_project_is_writable_and_exposes_name(self):
        """Test parent_project can be set (same org) and parent_project_name is returned read-only."""
        parent = KippoProject.objects.create(
            name="Parent Project",
            organization=self.organization,
            columnset=self.project.columnset,
            created_by=self.user,
            updated_by=self.user,
        )
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.patch(url, {"parent_project": str(parent.id)}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK, response.content)
        data = response.json()
        self.assertEqual(data["parent_project"], str(parent.id))
        self.assertEqual(data["parent_project_name"], "Parent Project")

    def test_parent_project_cross_organization_rejected(self):
        """Test a parent_project from a different organization is rejected."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        # self.other_project belongs to other_organization; superuser-free user can still send the id.
        response = self.client.patch(url, {"parent_project": str(self.other_project.id)}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("parent_project", response.json())

    def test_enable_cost_report_requires_slack_channel(self):
        """Test enable_cost_report cannot be turned on without a slack_channel_name (model.clean parity)."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        self.project.slack_channel_name = ""
        self.project.save()
        rejected = self.client.patch(url, {"enable_cost_report": True}, format="json")
        self.assertEqual(rejected.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("enable_cost_report", rejected.json())
        # With a slack channel supplied in the same request it succeeds.
        accepted = self.client.patch(url, {"enable_cost_report": True, "slack_channel_name": "proj-costs"}, format="json")
        self.assertEqual(accepted.status_code, HTTPStatus.OK, accepted.content)
        self.assertTrue(accepted.json()["enable_cost_report"])

    def test_meeting_fields_exposed_read_only(self):
        """Test meeting_calendar_url + meeting_description_tag are exposed and read-only."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        data = self.client.get(url).json()
        self.assertIn(f'[dsearch]{{"project":"{self.project.id}"}}[/dsearch]', data["meeting_description_tag"])
        self.assertIn("calendar.google.com", data["meeting_calendar_url"])
        # Read-only: attempting to write them is ignored (value stays derived).
        response = self.client.patch(url, {"meeting_description_tag": "tampered"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertNotEqual(response.json()["meeting_description_tag"], "tampered")

    def test_user_cannot_access_other_organization_projects(self):
        """Test that users can only access projects from their organizations."""
        url = f"{settings.URL_PREFIX}/api/projects/{self.other_project.id}/"
        response = self.client.get(url)

        # Should return 404 since the project is not in user's organization
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_pagination(self):
        """Test that pagination works correctly."""
        url = f"{settings.URL_PREFIX}/api/projects/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("count", data)
        self.assertIn("next", data)
        self.assertIn("previous", data)
        self.assertIn("results", data)

    def _create_extra_projects(self, count: int) -> None:
        """Helper to create N extra projects in self.organization for pagination tests."""
        for i in range(count):
            KippoProject.objects.create(
                name=f"Pagination Test Project {i}",
                organization=self.organization,
                columnset=self.project.columnset,
                created_by=self.github_manager,
                updated_by=self.github_manager,
            )

    def test_pagination_page_size_override_returns_requested_size(self):
        """Test that ?page_size=<N> returns up to N records (issue #204)."""
        baseline_count = self.client.get(f"{settings.URL_PREFIX}/api/projects/").json()["count"]
        self._create_extra_projects(4)

        url = f"{settings.URL_PREFIX}/api/projects/?page_size=3"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["count"], baseline_count + 4)
        self.assertEqual(len(data["results"]), 3)

    def test_pagination_page_size_default_when_not_provided(self):
        """Test that omitting page_size keeps the default of 50 (issue #204)."""
        url = f"{settings.URL_PREFIX}/api/projects/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        # Default page size is 50; the test org has well under 50 projects so page 1 is the only page.
        self.assertIsNone(data["next"])

    def test_pagination_page_size_capped_at_max_page_size(self):
        """Test that page_size is capped at max_page_size (200) (issue #204).

        Verifies the cap is enforced: ?page_size=10000 must return at most 200 items.
        We don't create 200+ rows here — DRF clamps the requested value to max_page_size
        before slicing, so the assertion is that the response is OK and the result size
        never exceeds the cap.
        """
        url = f"{settings.URL_PREFIX}/api/projects/?page_size=10000"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertLessEqual(len(data["results"]), 200)

    def test_openapi_schema_exposes_page_size_query_param(self):
        """Test that the generated OpenAPI schema documents the page_size query param (issue #204).

        drf-spectacular derives query parameters from the active pagination class. This test
        guards against regressions where the pagination class is replaced with one that lacks
        page_size_query_param — a silent kippo-ui client-generation breakage.
        """
        schema = SchemaGenerator().get_schema(request=None, public=True)
        projects_list_op = schema["paths"][f"{settings.URL_PREFIX}/api/projects/"]["get"]
        param_names = {p["name"] for p in projects_list_op.get("parameters", [])}

        self.assertIn("page_size", param_names)
        self.assertIn("page", param_names)


class ProjectWeeklyEffortViewSetTestCase(TestCase):
    """Test cases for ProjectWeeklyEffort REST API viewset."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Create another user in the same organization
        self.user2 = KippoUser.objects.create(
            username="testuser2",
            github_login="testuser2",
            email="testuser2@example.com",
            is_staff=True,
        )
        OrganizationMembership.objects.create(
            user=self.user2,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )

        # Create ProjectWeeklyEffort entries
        self.week1_start = datetime.date(2024, 1, 1)
        self.week2_start = datetime.date(2024, 1, 8)
        self.week3_start = datetime.date(2024, 1, 15)

        self.effort1 = ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=self.week1_start,
            hours=40,
            created_by=self.user,
            updated_by=self.user,
        )
        self.effort2 = ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=self.week2_start,
            hours=35,
            created_by=self.user,
            updated_by=self.user,
        )
        self.effort3 = ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user2,
            week_start=self.week1_start,
            hours=38,
            created_by=self.user2,
            updated_by=self.user2,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_weekly_effort(self):
        """Test listing weekly effort entries."""
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("results", data)
        # User should see all effort entries from their organization
        self.assertEqual(data["count"], 3)

    def test_retrieve_weekly_effort(self):
        """Test retrieving a single weekly effort entry."""
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/{self.effort1.id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["id"], self.effort1.id)
        self.assertEqual(data["project_name"], self.project.name)
        self.assertEqual(data["user_username"], self.user.username)
        self.assertEqual(data["hours"], 40)

    def test_filter_by_project(self):
        """Test filtering weekly effort by project."""
        # Create another project and effort entry
        other_project = KippoProject.objects.create(
            name="Other Project",
            organization=self.organization,
            columnset=self.project.columnset,
            created_by=self.user,
            updated_by=self.user,
        )
        ProjectWeeklyEffort.objects.create(
            project=other_project,
            user=self.user,
            week_start=self.week1_start,
            hours=20,
            created_by=self.user,
            updated_by=self.user,
        )

        # Filter by original project
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/?project={self.project.id}"
        response = self.client.get(url)
        data = response.json()
        self.assertEqual(data["count"], 3)
        for result in data["results"]:
            self.assertEqual(result["project"], str(self.project.id))

    def test_filter_by_user(self):
        """Test filtering weekly effort by user."""
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/?user={self.user.id}"
        response = self.client.get(url)
        data = response.json()
        self.assertEqual(data["count"], 2)
        for result in data["results"]:
            self.assertEqual(result["user"], str(self.user.id))

    def test_filter_by_project_and_user(self):
        """Test filtering weekly effort by both project and user."""
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/?project={self.project.id}&user={self.user.id}"
        response = self.client.get(url)
        data = response.json()
        self.assertEqual(data["count"], 2)
        for result in data["results"]:
            self.assertEqual(result["project"], str(self.project.id))
            self.assertEqual(result["user"], str(self.user.id))

    def test_pagination(self):
        """Test that pagination works correctly."""
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("count", data)
        self.assertIn("next", data)
        self.assertIn("previous", data)
        self.assertIn("results", data)

    def test_create_negative_hours_rejected(self):
        """Negative hours must be rejected by the API (guards the direct-API path)."""
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/"
        data = {"project": str(self.project.id), "week_start": "2024-02-01", "hours": -1}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("hours", response.json())

    def test_create_hours_over_weekly_max_rejected(self):
        """Hours greater than the hours in a week (7 * 24) must be rejected."""
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/"
        data = {"project": str(self.project.id), "week_start": "2024-02-01", "hours": 169}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("hours", response.json())

    def test_update_to_negative_hours_rejected(self):
        """Patching an existing entry to negative hours must be rejected."""
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/{self.effort1.id}/"
        response = self.client.patch(url, {"hours": -1}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.effort1.refresh_from_db()
        self.assertEqual(self.effort1.hours, 40)

    @freeze_time("2024-02-05")  # within the editable window for week 2024-02-01 (monthly close: 2024-03-11 12:05 JST)
    def test_create_zero_hours_allowed(self):
        """Zero hours is permitted (consistent with the UI's `hours >= 0` create filter)."""
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/"
        data = {"project": str(self.project.id), "week_start": "2024-02-01", "hours": 0}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED)


class OpenAPISchemaTestCase(TestCase):
    """Test cases for OpenAPI schema generation."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_schema_endpoint(self):
        """Test that OpenAPI schema endpoint is accessible."""
        url = f"{settings.URL_PREFIX}/api/schema/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_swagger_ui_endpoint(self):
        """Test that Swagger UI endpoint is accessible."""
        url = f"{settings.URL_PREFIX}/api/docs/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_schema_endpoint_with_session_auth(self):
        """Test that schema endpoint works with Django session authentication."""
        # Create a new client without force_authenticate
        client = APIClient()
        # Login using Django session
        client.force_login(self.user)

        url = f"{settings.URL_PREFIX}/api/schema/"
        response = client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_swagger_ui_endpoint_with_session_auth(self):
        """Test that Swagger UI endpoint works with Django session authentication."""
        # Create a new client without force_authenticate
        client = APIClient()
        # Login using Django session
        client.force_login(self.user)

        url = f"{settings.URL_PREFIX}/api/docs/"
        response = client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_schema_endpoint_with_jwt_auth(self):
        """Test that schema endpoint works with JWT authentication."""
        # Create a new client
        client = APIClient()
        # Get JWT token
        refresh = RefreshToken.for_user(self.user)
        access_token = str(refresh.access_token)

        # Use JWT authentication
        url = f"{settings.URL_PREFIX}/api/schema/"
        response = client.get(url, HTTP_AUTHORIZATION=f"Bearer {access_token}")
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_swagger_ui_endpoint_with_jwt_auth(self):
        """Test that Swagger UI endpoint works with JWT authentication."""
        # Create a new client
        client = APIClient()
        # Get JWT token
        refresh = RefreshToken.for_user(self.user)
        access_token = str(refresh.access_token)

        # Use JWT authentication
        url = f"{settings.URL_PREFIX}/api/docs/"
        response = client.get(url, HTTP_AUTHORIZATION=f"Bearer {access_token}")
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_schema_endpoint_accessible_without_authentication(self):
        """Test that schema endpoint is publicly accessible (no auth required)."""
        # Create a new client without authentication
        client = APIClient()

        url = f"{settings.URL_PREFIX}/api/schema/"
        response = client.get(url)
        # Schema endpoint is intentionally public to allow API documentation access
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_swagger_ui_endpoint_accessible_without_authentication(self):
        """Test that Swagger UI endpoint is publicly accessible (no auth required)."""
        # Create a new client without authentication
        client = APIClient()

        url = f"{settings.URL_PREFIX}/api/docs/"
        response = client.get(url)
        # Swagger UI endpoint is intentionally public to allow API documentation access
        self.assertEqual(response.status_code, HTTPStatus.OK)


class PermissionsTestCase(TestCase):
    """Test cases for API permissions (superuser-only Create/Delete)."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Create a superuser
        self.superuser = KippoUser.objects.create(
            username="superuser",
            github_login="superuser",
            email="superuser@example.com",
            is_staff=True,
            is_superuser=True,
        )
        OrganizationMembership.objects.create(
            user=self.superuser,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )

        # Create weekly effort for testing
        self.effort = ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=datetime.date(2024, 1, 1),
            hours=40,
            created_by=self.user,
            updated_by=self.user,
        )

        self.client = APIClient()

    def test_regular_user_can_create_project_in_own_org(self):
        """Org-member can POST /api/projects/ for an org they belong to (kippo#284)."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/"
        data = {
            "name": "Org Member Project",
            "organization": str(self.organization.id),
            "columnset": self.project.columnset.id,
            **_registration_fields(self.organization, self.user),
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED)

        created = KippoProject.objects.get(name="Org Member Project")
        # created_by/updated_by auto-set from request.user (kippo#284 decision #5)
        self.assertEqual(created.created_by, self.user)
        self.assertEqual(created.updated_by, self.user)

    def test_regular_user_cannot_create_project_in_other_org(self):
        """Org-member cannot POST /api/projects/ for an org they don't belong to (kippo#284)."""
        other_org = KippoOrganization.objects.create(
            name="permissions-other-org",
            github_organization_name="permissions-other-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/"
        data = {
            "name": "Forbidden Cross-Org Project",
            "organization": str(other_org.id),
            "columnset": self.project.columnset.id,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)
        self.assertFalse(KippoProject.objects.filter(name="Forbidden Cross-Org Project").exists())

    def test_regular_user_post_without_organization_is_forbidden(self):
        """POST without an organization field cannot pass org-membership check (kippo#284)."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/"
        data = {
            "name": "Missing Org Project",
            "columnset": self.project.columnset.id,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_superuser_can_create_project(self):
        """Test that superusers can create projects."""
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/projects/"
        data = {
            "name": "Superuser Project",
            "organization": str(self.organization.id),
            "columnset": self.project.columnset.id,
            **_registration_fields(self.organization, self.superuser),
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED)

    def test_create_missing_required_registration_fields_rejected(self):
        """Registration requires customer/PM/start_date/target_date (kippo#40 / T19)."""
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/projects/"
        data = {
            "name": "Incomplete Project",
            "organization": str(self.organization.id),
            "columnset": self.project.columnset.id,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        for field in ("customer", "project_manager", "start_date", "target_date"):
            self.assertIn(field, response.json())

    def test_edit_existing_project_not_blocked_by_registration_requirements(self):
        """The required-field validation is create-only; editing a contract-less / customer-less
        project must still succeed (kippo#40 / T19).
        """
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        # self.project (from setup_basic_project) has no customer/PM; a PATCH must not be blocked
        response = self.client.patch(url, {"name": "Renamed Project"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.json()["name"], "Renamed Project")

    def test_superuser_can_create_project_in_any_org(self):
        """Superusers retain unrestricted create across orgs (kippo#284)."""
        other_org = KippoOrganization.objects.create(
            name="superuser-foreign-org",
            github_organization_name="superuser-foreign-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/projects/"
        data = {
            "name": "Superuser Cross-Org Project",
            "organization": str(other_org.id),
            "columnset": self.project.columnset.id,
            **_registration_fields(other_org, self.superuser),
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED)

    def test_regular_user_cannot_delete_project(self):
        """Test that regular authenticated users cannot delete projects."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_superuser_can_delete_project(self):
        """Test that superusers can delete projects."""
        # Create a project to delete
        test_project = KippoProject.objects.create(
            name="Project to Delete",
            organization=self.organization,
            columnset=self.project.columnset,
            created_by=self.superuser,
            updated_by=self.superuser,
        )
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/projects/{test_project.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, HTTPStatus.NO_CONTENT)

    def test_regular_user_can_read_project(self):
        """Test that regular authenticated users can read projects."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_regular_user_can_update_project(self):
        """Test that regular authenticated users can update projects."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        data = {"name": "Updated Project Name"}
        response = self.client.patch(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        # updated_by auto-set from request.user (kippo#284 decision #5)
        self.project.refresh_from_db()
        self.assertEqual(self.project.updated_by, self.user)

    def test_regular_user_cannot_update_other_org_project(self):
        """PATCH against a project in a foreign org is rejected (kippo#284, queryset-scoped → 404)."""
        other_org = KippoOrganization.objects.create(
            name="cross-org-patch-org",
            github_organization_name="cross-org-patch-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        foreign_project = KippoProject.objects.create(
            name="Foreign Project",
            organization=other_org,
            columnset=self.project.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/{foreign_project.id}/"
        response = self.client.patch(url, {"name": "Hijack Attempt"}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
        foreign_project.refresh_from_db()
        self.assertEqual(foreign_project.name, "Foreign Project")

    @freeze_time("2024-02-05")  # within the editable window for week 2024-02-01 (monthly close: 2024-03-11 12:05 JST)
    def test_regular_user_can_create_own_weekly_effort(self):
        """Test that regular authenticated users can create their own weekly effort."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/"
        data = {
            "project": str(self.project.id),
            "week_start": "2024-02-01",
            "hours": 35,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        # Verify user is auto-set to the authenticated user
        self.assertEqual(response.data["user"], self.user.id)

    def test_superuser_can_create_weekly_effort(self):
        """Test that superusers can create weekly effort."""
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/"
        data = {
            "project": str(self.project.id),
            "user": self.user.id,
            "week_start": "2024-02-01",
            "hours": 35,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED)

    def test_regular_user_cannot_delete_weekly_effort(self):
        """Test that regular authenticated users cannot delete weekly effort."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/{self.effort.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_superuser_can_delete_weekly_effort(self):
        """Test that superusers can delete weekly effort."""
        # Create an effort to delete
        test_effort = ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=datetime.date(2024, 2, 1),
            hours=30,
            created_by=self.superuser,
            updated_by=self.superuser,
        )
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/{test_effort.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, HTTPStatus.NO_CONTENT)

    def test_regular_user_can_read_weekly_effort(self):
        """Test that regular authenticated users can read weekly effort."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/{self.effort.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    @freeze_time("2024-01-04")  # within the editable window for week 2024-01-01 (monthly close: 2024-02-12 12:05 JST)
    def test_regular_user_can_update_weekly_effort(self):
        """Test that regular authenticated users can update weekly effort."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/{self.effort.id}/"
        data = {"hours": 45}
        response = self.client.patch(url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.OK)


class OrganizationScopedAuthorizationTestCase(TestCase):
    """Test cases for organization-scoped authorization (superuser vs regular user access)."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        """Set up test data."""
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Create a second organization
        self.other_organization = KippoOrganization.objects.create(
            name="other-org",
            github_organization_name="other-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # Create a project in the other organization
        self.other_project = KippoProject.objects.create(
            name="Other Org Project",
            organization=self.other_organization,
            columnset=self.project.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # Create a user in the other organization (regular user)
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

        # Create a superuser (not in any organization initially)
        self.superuser = KippoUser.objects.create(
            username="superuser",
            github_login="superuser",
            email="superuser@example.com",
            is_staff=True,
            is_superuser=True,
        )

        # Create weekly effort for both projects
        self.effort = ProjectWeeklyEffort.objects.create(
            project=self.project,
            user=self.user,
            week_start=datetime.date(2024, 1, 1),
            hours=40,
            created_by=self.user,
            updated_by=self.user,
        )

        self.other_effort = ProjectWeeklyEffort.objects.create(
            project=self.other_project,
            user=self.other_user,
            week_start=datetime.date(2024, 1, 1),
            hours=35,
            created_by=self.other_user,
            updated_by=self.other_user,
        )

        self.client = APIClient()

    def test_regular_user_sees_only_own_organization_projects(self):
        """Test that regular users only see projects from their organizations."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        project_ids = [result["id"] for result in data["results"]]

        # User should see their organization's project
        self.assertIn(str(self.project.id), project_ids)
        # User should NOT see other organization's project
        self.assertNotIn(str(self.other_project.id), project_ids)

    def test_superuser_sees_all_projects(self):
        """Test that superusers see projects from all organizations."""
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/projects/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        project_ids = [result["id"] for result in data["results"]]

        # Superuser should see projects from both organizations
        self.assertIn(str(self.project.id), project_ids)
        self.assertIn(str(self.other_project.id), project_ids)

    def test_regular_user_cannot_retrieve_other_organization_project(self):
        """Test that regular users cannot retrieve projects from other organizations."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/{self.other_project.id}/"
        response = self.client.get(url)

        # Should return 404 since project is not in user's organization
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_superuser_can_retrieve_any_organization_project(self):
        """Test that superusers can retrieve projects from any organization."""
        self.client.force_authenticate(user=self.superuser)

        # Retrieve project from first organization
        url = f"{settings.URL_PREFIX}/api/projects/{self.project.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

        # Retrieve project from other organization
        url = f"{settings.URL_PREFIX}/api/projects/{self.other_project.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_regular_user_sees_only_own_organization_weekly_effort(self):
        """Test that regular users only see weekly effort from their organizations."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        effort_ids = [result["id"] for result in data["results"]]

        # User should see their organization's effort
        self.assertIn(self.effort.id, effort_ids)
        # User should NOT see other organization's effort
        self.assertNotIn(self.other_effort.id, effort_ids)

    def test_superuser_sees_all_weekly_effort(self):
        """Test that superusers see weekly effort from all organizations."""
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        effort_ids = [result["id"] for result in data["results"]]

        # Superuser should see effort from both organizations
        self.assertIn(self.effort.id, effort_ids)
        self.assertIn(self.other_effort.id, effort_ids)

    def test_regular_user_cannot_retrieve_other_organization_weekly_effort(self):
        """Test that regular users cannot retrieve weekly effort from other organizations."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/{self.other_effort.id}/"
        response = self.client.get(url)

        # Should return 404 since effort is for project not in user's organization
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_superuser_can_retrieve_any_organization_weekly_effort(self):
        """Test that superusers can retrieve weekly effort from any organization."""
        self.client.force_authenticate(user=self.superuser)

        # Retrieve effort from first organization
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/{self.effort.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

        # Retrieve effort from other organization
        url = f"{settings.URL_PREFIX}/api/projects/weeklyeffort/{self.other_effort.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)


class ProjectColumnsetDefaultTestCase(TestCase):
    """`columnset` is optional on create — it resolves from the organization's default columnset.

    Resolution order (KippoOrganization.get_default_columnset): explicit `default_columnset` ->
    first org-specific columnset -> first global (org-null) columnset.
    """

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.global_columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.client = APIClient()
        self.url = f"{settings.URL_PREFIX}/api/projects/"

    def test_get_default_columnset_prefers_explicit_default(self):
        ProjectColumnSet.objects.create(name="org-specific cs", organization=self.organization)
        explicit = ProjectColumnSet.objects.create(name="explicit default cs", organization=self.organization)
        self.organization.default_columnset = explicit
        self.organization.save()
        self.assertEqual(self.organization.get_default_columnset(), explicit)

    def test_get_default_columnset_prefers_org_specific_over_global(self):
        org_columnset = ProjectColumnSet.objects.create(name="org-specific cs", organization=self.organization)
        self.assertEqual(self.organization.get_default_columnset(), org_columnset)

    def test_get_default_columnset_falls_back_to_global(self):
        self.assertEqual(self.organization.get_default_columnset(), self.global_columnset)

    def test_create_project_without_columnset_applies_org_default(self):
        explicit = ProjectColumnSet.objects.create(name="explicit default cs", organization=self.organization)
        self.organization.default_columnset = explicit
        self.organization.save()
        self.client.force_authenticate(user=self.user)
        data = {"name": "Default Columnset Project", "organization": str(self.organization.id), **_registration_fields(self.organization, self.user)}
        response = self.client.post(self.url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        created = KippoProject.objects.get(name="Default Columnset Project")
        self.assertEqual(created.columnset_id, explicit.id)

    def test_create_project_without_columnset_falls_back_to_global(self):
        self.client.force_authenticate(user=self.user)
        data = {"name": "Fallback Columnset Project", "organization": str(self.organization.id), **_registration_fields(self.organization, self.user)}
        response = self.client.post(self.url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        created = KippoProject.objects.get(name="Fallback Columnset Project")
        self.assertEqual(created.columnset_id, self.global_columnset.id)

    def test_create_project_with_cross_org_columnset_rejected(self):
        other_org = KippoOrganization.objects.create(
            name="cross-org-columnset-org",
            github_organization_name="cross-org-columnset-org",
            created_by=self.user,
            updated_by=self.user,
        )
        other_columnset = ProjectColumnSet.objects.create(name="other org cs", organization=other_org)
        self.client.force_authenticate(user=self.user)
        data = {
            "name": "Cross Org Columnset Project",
            "organization": str(self.organization.id),
            "columnset": str(other_columnset.id),
        }
        response = self.client.post(self.url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("columnset", response.json())

    def test_create_project_without_columnset_and_no_default_returns_400(self):
        self.client.force_authenticate(user=self.user)
        data = {"name": "Unresolvable Columnset Project", "organization": str(self.organization.id)}
        with mock.patch.object(KippoOrganization, "get_default_columnset", return_value=None):
            response = self.client.post(self.url, data, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("columnset", response.json())

    def test_patch_organization_rejects_now_cross_org_existing_columnset(self):
        """Re-parenting to an org the existing org-specific columnset doesn't belong to is rejected."""
        # Project starts in self.organization with an org-specific columnset.
        org_columnset = ProjectColumnSet.objects.create(name="org-a cs", organization=self.organization)
        project = KippoProject.objects.create(
            name="Reparented Project",
            organization=self.organization,
            columnset=org_columnset,
            created_by=self.user,
            updated_by=self.user,
        )
        # A second org the user also belongs to.
        other_org = KippoOrganization.objects.create(
            name="reparent-target-org",
            github_organization_name="reparent-target-org",
            created_by=self.user,
            updated_by=self.user,
        )
        OrganizationMembership.objects.create(user=self.user, organization=other_org, is_developer=True, created_by=self.user, updated_by=self.user)
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(f"{self.url}{project.id}/", {"organization": str(other_org.id)}, format="json")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("columnset", response.json())


class ContractAndBillingEntryAPITestCase(TestCase):
    """Contract + billing-entry REST endpoints nested under projects/ (kippo#31)."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # a project in another organization the user does NOT belong to (scoping check)
        self.other_org = KippoOrganization.objects.create(
            name="contract-api-other-org",
            github_organization_name="contract-api-other-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_project = KippoProject.objects.create(
            name="Other Project",
            organization=self.other_org,
            columnset=self.project.columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.base = f"{settings.URL_PREFIX}/api/projects"

    def test_create_read_update_contract(self):
        url = f"{self.base}/{self.project.id}/contract/"
        resp = self.client.post(
            url, {"billing_type": "delivery", "pricing_basis": "fixed", "total_amount": "1500000", "end_date": "2026-09-30"}, format="json"
        )
        self.assertEqual(resp.status_code, HTTPStatus.CREATED, resp.content)
        self.assertEqual(KippoProjectContract.objects.filter(project=self.project).count(), 1)
        # the project (from the URL) is bound server-side, not the payload
        self.assertEqual(resp.json()["project"], str(self.project.id))
        listing = self.client.get(url).json()
        self.assertEqual(len(listing["results"]), 1)
        contract_id = resp.json()["id"]
        patch = self.client.patch(f"{url}{contract_id}/", {"total_amount": "2000000"}, format="json")
        self.assertEqual(patch.status_code, HTTPStatus.OK, patch.content)
        self.assertEqual(self.project.contract.total_amount, 2000000)

    def test_second_contract_rejected(self):
        url = f"{self.base}/{self.project.id}/contract/"
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type="delivery",
            pricing_basis="fixed",
            total_amount=1,
            end_date="2026-09-30",
            created_by=self.user,
            updated_by=self.user,
        )
        resp = self.client.post(url, {"billing_type": "delivery", "pricing_basis": "fixed", "total_amount": "5"}, format="json")
        self.assertEqual(resp.status_code, HTTPStatus.BAD_REQUEST)

    def test_billing_entry_crud_and_received_by_stamp(self):
        # effort/no-effort -> empty ledger on creation, so the POSTed entry below is the only one
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type="delivery",
            pricing_basis="effort",
            total_amount=None,
            end_date="2026-09-30",
            created_by=self.user,
            updated_by=self.user,
        )
        url = f"{self.base}/{self.project.id}/billing-entries/"
        resp = self.client.post(url, {"billing_date": "2026-09-30", "amount": "1000000", "is_received": True}, format="json")
        self.assertEqual(resp.status_code, HTTPStatus.CREATED, resp.content)
        entry = self.project.contract.billing_entries.get()
        self.assertEqual(entry.received_by, self.user)  # stamped from the acting user
        self.assertIsNotNone(entry.received_datetime)  # auto-set by model.save()

    def test_billing_entry_requires_contract(self):
        url = f"{self.base}/{self.project.id}/billing-entries/"
        resp = self.client.post(url, {"billing_date": "2026-09-30", "amount": "1"}, format="json")
        self.assertEqual(resp.status_code, HTTPStatus.BAD_REQUEST)

    def test_contract_org_scoped(self):
        KippoProjectContract.objects.create(
            project=self.other_project,
            billing_type="delivery",
            pricing_basis="fixed",
            total_amount=1,
            end_date="2026-09-30",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        listing = self.client.get(f"{self.base}/{self.other_project.id}/contract/").json()
        self.assertEqual(listing["results"], [])
        resp = self.client.post(
            f"{self.base}/{self.other_project.id}/contract/",
            {"billing_type": "delivery", "pricing_basis": "fixed", "total_amount": "1"},
            format="json",
        )
        self.assertEqual(resp.status_code, HTTPStatus.NOT_FOUND)

    def test_fixed_contract_requires_total_amount(self):
        # mirror KippoProjectContract.clean(): fixed pricing without total_amount → 400, not a saved
        # contract that later breaks billing generation.
        url = f"{self.base}/{self.project.id}/contract/"
        resp = self.client.post(url, {"billing_type": "delivery", "pricing_basis": "fixed", "end_date": "2026-09-30"}, format="json")
        self.assertEqual(resp.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("total_amount", resp.json())

    def test_contract_start_after_end_rejected(self):
        url = f"{self.base}/{self.project.id}/contract/"
        resp = self.client.post(
            url,
            {"billing_type": "delivery", "pricing_basis": "fixed", "total_amount": "1", "start_date": "2026-10-01", "end_date": "2026-09-30"},
            format="json",
        )
        self.assertEqual(resp.status_code, HTTPStatus.BAD_REQUEST)

    def test_duplicate_billing_entry_rejected_cleanly(self):
        # effort/no-effort -> empty ledger on creation, so the first POST below is the only entry
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type="delivery",
            pricing_basis="effort",
            total_amount=None,
            end_date="2026-09-30",
            created_by=self.user,
            updated_by=self.user,
        )
        url = f"{self.base}/{self.project.id}/billing-entries/"
        first = self.client.post(url, {"billing_date": "2026-09-30", "amount": "1000000"}, format="json")
        self.assertEqual(first.status_code, HTTPStatus.CREATED, first.content)
        # same (contract, billing_date) → clean 400 (the unique constraint would otherwise 500)
        dup = self.client.post(url, {"billing_date": "2026-09-30", "amount": "5"}, format="json")
        self.assertEqual(dup.status_code, HTTPStatus.BAD_REQUEST)
