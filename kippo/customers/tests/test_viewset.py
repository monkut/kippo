from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import KippoCustomer


class KippoCustomerViewSetTestCase(TestCase):
    """Test cases for KippoCustomer REST API viewset."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Second organization + user
        self.other_organization = KippoOrganization.objects.create(
            name="other-test-org",
            github_organization_name="other-test-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
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

        # Superuser
        self.superuser = KippoUser.objects.create(
            username="su",
            email="su@example.com",
            is_staff=True,
            is_superuser=True,
        )

        # Two customers, one per org
        self.customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="Acme Corp",
            email="acme@example.com",
            created_by=self.user,
            updated_by=self.user,
        )
        self.other_customer = KippoCustomer.objects.create(
            organization=self.other_organization,
            name="Globex",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()

    def test_user_a_in_org_x_sees_only_org_x_customers(self):
        """A user in org X sees only org X customers in GET /customers/."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/customers/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        customer_ids = [result["id"] for result in data["results"]]
        self.assertIn(str(self.customer.id), customer_ids)
        self.assertNotIn(str(self.other_customer.id), customer_ids)

    def test_user_in_two_orgs_sees_both_orgs_customers(self):
        """A user belonging to two orgs sees customers from both."""
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/customers/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        customer_ids = [result["id"] for result in data["results"]]
        self.assertIn(str(self.customer.id), customer_ids)
        self.assertIn(str(self.other_customer.id), customer_ids)

    def test_superuser_sees_all_customers(self):
        """Superuser sees all customers across all organizations."""
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/customers/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        customer_ids = [result["id"] for result in data["results"]]
        self.assertIn(str(self.customer.id), customer_ids)
        self.assertIn(str(self.other_customer.id), customer_ids)

    def test_non_member_get_other_org_customer_returns_404(self):
        """Non-member requesting GET /customers/{other-org-id}/ gets 404 (not 403)."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/customers/{self.other_customer.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_non_superuser_post_other_org_rejected_400(self):
        """Non-superuser POST with org they don't belong to is rejected 400."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/customers/"
        response = self.client.post(
            url,
            {
                "organization": str(self.other_organization.id),
                "name": "Forbidden Customer",
            },
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("organization", response.json())

    def test_non_superuser_post_own_org_succeeds_201(self):
        """Non-superuser POST succeeds for an org they belong to."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/customers/"
        response = self.client.post(
            url,
            {
                "organization": str(self.organization.id),
                "name": "New Customer Co",
                "email": "new@example.com",
            },
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        data = response.json()
        self.assertEqual(data["name"], "New Customer Co")
        self.assertEqual(data["organization"], str(self.organization.id))
        self.assertEqual(data["organization_name"], self.organization.name)

    def test_non_superuser_patch_cannot_reassign_organization(self):
        """Non-superuser PATCH cannot reassign customer to an org they don't belong to."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/customers/{self.customer.id}/"
        response = self.client.patch(
            url,
            {"organization": str(self.other_organization.id)},
            format="json",
        )
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("organization", response.json())

    def test_non_superuser_delete_returns_403(self):
        """Non-superuser DELETE returns 403."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/customers/{self.customer.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_superuser_delete_succeeds_204(self):
        """Superuser DELETE succeeds with 204."""
        self.client.force_authenticate(user=self.superuser)
        url = f"{settings.URL_PREFIX}/api/customers/{self.customer.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, HTTPStatus.NO_CONTENT)
        self.assertFalse(KippoCustomer.objects.filter(id=self.customer.id).exists())

    def test_filter_is_active_false_returns_inactive_only(self):
        """`?is_active=false` returns only customers with display_as_active=False."""
        inactive = KippoCustomer.objects.create(
            organization=self.organization,
            name="Inactive Customer",
            display_as_active=False,
            created_by=self.user,
            updated_by=self.user,
        )
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/customers/?is_active=false"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        customer_ids = [result["id"] for result in data["results"]]
        self.assertIn(str(inactive.id), customer_ids)
        self.assertNotIn(str(self.customer.id), customer_ids)
