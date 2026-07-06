from datetime import date, timedelta
from decimal import Decimal
from http import HTTPStatus

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from commons.tests import DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES, default_project_category, setup_basic_project
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from projects.models import KippoProject, KippoProjectBillingEntry, KippoProjectContract, ProjectColumnSet
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

    def test_contract_folder_url_roundtrips_through_api(self):
        """A contract_folder_url set via PATCH is returned by GET."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/customers/{self.customer.id}/"
        contract_folder_url = "https://drive.example.com/contracts/acme"
        patch_response = self.client.patch(
            url,
            {"contract_folder_url": contract_folder_url},
            format="json",
        )
        self.assertEqual(patch_response.status_code, HTTPStatus.OK)
        self.assertEqual(patch_response.json()["contract_folder_url"], contract_folder_url)

        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, HTTPStatus.OK)
        self.assertEqual(get_response.json()["contract_folder_url"], contract_folder_url)

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


class KippoCustomerChangelistParityTestCase(TestCase):
    """Changelist-parity API additions (kippo#45): list aggregates + compliance, recent_ending filter,
    active-projects action, and the per-organization fiscal-year-summary action.
    """

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        # FY starts in January → current FY = this (JST) calendar year; predictable date math.
        self.organization.fiscalyear_start_month = 1
        self.organization.save()
        self.user = created["KippoUser"]
        self.github_manager = KippoUser.objects.get(username="github-manager")
        self.columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)

        # second org + a user who only belongs to it (for org-scoping checks)
        self.other_organization = KippoOrganization.objects.create(
            name="other-test-org",
            github_organization_name="other-test-org",
            fiscalyear_start_month=1,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_user = KippoUser.objects.create(username="otheruser", github_login="otheruser", email="otheruser@example.com", is_staff=True)
        OrganizationMembership.objects.create(
            user=self.other_user,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )

        self.customer = KippoCustomer.objects.create(organization=self.organization, name="Acme Corp", created_by=self.user, updated_by=self.user)
        self.empty_customer = KippoCustomer.objects.create(
            organization=self.organization, name="Zeta (no projects)", created_by=self.user, updated_by=self.user
        )
        self.other_customer = KippoCustomer.objects.create(
            organization=self.other_organization, name="Globex", created_by=self.github_manager, updated_by=self.github_manager
        )
        self.today = timezone.localdate()
        self.fy_start = date(self.today.year, 1, 1)
        self.client = APIClient()

    def _make_project(self, name: str, customer: KippoCustomer, target_date: date, **kwargs) -> KippoProject:
        return KippoProject.objects.create(
            organization=customer.organization,
            name=name,
            category=default_project_category(),
            columnset=self.columnset,
            customer=customer,
            target_date=target_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            **kwargs,
        )

    def _make_contract(self, project: KippoProject, total: str | None, end_date: date, **kwargs) -> KippoProjectContract:
        return KippoProjectContract.objects.create(
            project=project,
            billing_type=kwargs.pop("billing_type", "delivery"),
            pricing_basis=kwargs.pop("pricing_basis", "fixed" if total else "effort"),
            total_amount=Decimal(total) if total is not None else None,
            end_date=end_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            **kwargs,
        )

    def _result_for(self, data: dict, customer: KippoCustomer) -> dict:
        return next(r for r in data["results"] if r["id"] == str(customer.id))

    # --- (a) list scalar fields ------------------------------------------------------------------

    def test_list_returns_new_scalar_fields_for_customer_with_active_projects(self):
        active = self._make_project("acme-active", self.customer, date(self.today.year, 9, 30))
        self._make_contract(active, "1500000", date(self.today.year, 9, 30))
        # a closed project should not count toward active_project_count / contract total
        closed = self._make_project("acme-closed", self.customer, date(self.today.year, 8, 31), is_closed=True)
        self._make_contract(closed, "9999999", date(self.today.year, 8, 31))

        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        row = self._result_for(response.json(), self.customer)
        self.assertEqual(row["active_project_count"], 1)
        self.assertEqual(Decimal(row["active_projects_contract_total"]), Decimal("1500000"))
        self.assertFalse(row["compliance_verified"])

    def test_list_zero_aggregates_for_customer_without_projects(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/")
        row = self._result_for(response.json(), self.empty_customer)
        self.assertEqual(row["active_project_count"], 0)
        self.assertEqual(Decimal(row["active_projects_contract_total"]), Decimal("0"))

    def test_list_compliance_verified_true_when_check_verified(self):
        check = self.customer.compliance_check
        check.verified = True
        check.save()
        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/")
        self.assertTrue(self._result_for(response.json(), self.customer)["compliance_verified"])

    # --- (b) recent_ending filter ----------------------------------------------------------------

    def test_recent_ending_includes_customer_with_project_in_window(self):
        # target_date in the current FY → within [prev-FY-start, current-FY-end)
        self._make_project("acme-this-fy", self.customer, date(self.today.year, 6, 30))
        # other customer's project ends well outside the window (next FY) → excluded
        self._make_project("zeta-future", self.empty_customer, date(self.today.year + 2, 6, 30))

        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/?recent_ending=true")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ids = [r["id"] for r in response.json()["results"]]
        self.assertIn(str(self.customer.id), ids)
        self.assertNotIn(str(self.empty_customer.id), ids)

    def test_recent_ending_excludes_project_before_window(self):
        # target_date one day before the previous-FY start → outside the 2-FY window
        prev_fy_start = date(self.today.year - 1, 1, 1)
        self._make_project("acme-old", self.customer, prev_fy_start - timedelta(days=1))
        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/?recent_ending=true")
        self.assertNotIn(str(self.customer.id), [r["id"] for r in response.json()["results"]])

    # --- (c) active-projects action --------------------------------------------------------------

    def test_active_projects_action_rows_and_received_total_honors_fy_cutoff(self):
        project = self._make_project("acme-billed", self.customer, date(self.today.year, 9, 30))
        contract = self._make_contract(project, "2000000", date(self.today.year, 9, 30))
        # received on/after FY start → counted
        KippoProjectBillingEntry.objects.create(contract=contract, billing_date=self.fy_start, amount=Decimal("500000"), is_received=True)
        # received but before FY start → excluded by the cutoff
        KippoProjectBillingEntry.objects.create(
            contract=contract, billing_date=self.fy_start - timedelta(days=1), amount=Decimal("900000"), is_received=True
        )
        # within FY but not received → excluded
        KippoProjectBillingEntry.objects.create(
            contract=contract, billing_date=self.fy_start + timedelta(days=5), amount=Decimal("700000"), is_received=False
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/{self.customer.id}/active-projects/")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], str(project.id))
        self.assertEqual(row["name"], "acme-billed")
        self.assertEqual(Decimal(row["contract_amount"]), Decimal("2000000"))
        self.assertEqual(row["contract_end_date"], date(self.today.year, 9, 30).isoformat())
        self.assertEqual(Decimal(row["received_total_current_fy"]), Decimal("500000"))

    def test_active_projects_action_null_contract_fields_without_contract(self):
        self._make_project("acme-no-contract", self.customer, date(self.today.year, 9, 30))
        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/{self.customer.id}/active-projects/")
        row = response.json()[0]
        self.assertIsNone(row["contract_amount"])
        self.assertIsNone(row["contract_end_date"])
        self.assertEqual(Decimal(row["received_total_current_fy"]), Decimal("0"))

    # --- (d) fiscal-year-summary action ----------------------------------------------------------

    def test_fiscal_year_summary_totals_and_monthly_breakdown(self):
        # delivery contract ending in March → whole total in its end month; received entry counted
        delivery = self._make_project("delivery", self.customer, date(self.today.year, 3, 31))
        delivery_contract = self._make_contract(delivery, "3000000", date(self.today.year, 3, 31))
        # the contract auto-generates a 3,000,000 entry at end-of-March on creation; record a
        # 1,000,000 partial receipt against it (planned stays 3,000,000 from the terms)
        entry = delivery_contract.billing_entries.get(billing_date=date(self.today.year, 3, 31))
        entry.amount = Decimal("1000000")
        entry.is_received = True
        entry.save()
        # monthly fixed Apr–Jun (3 months) → 1,200,000 split 400,000/month
        monthly = self._make_project("monthly", self.customer, date(self.today.year, 6, 30))
        self._make_contract(monthly, "1200000", date(self.today.year, 6, 30), billing_type="monthly", start_date=date(self.today.year, 4, 1))

        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/fiscal-year-summary/")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        summary = next(s for s in response.json() if s["organization"]["name"] == self.organization.name)
        self.assertEqual(summary["organization"]["id"], str(self.organization.id))
        self.assertEqual(summary["fiscal_year_start"], self.fy_start.isoformat())
        self.assertEqual(summary["fiscal_year_end"], date(self.today.year + 1, 1, 1).isoformat())
        self.assertEqual(summary["project_count"], 2)  # both contracts end this FY
        self.assertEqual(Decimal(summary["planned_total"]), Decimal("4200000"))
        self.assertEqual(Decimal(summary["received_total"]), Decimal("1000000"))
        breakdown = {row["month"]: Decimal(row["amount"]) for row in summary["monthly_planned_breakdown"]}
        self.assertEqual(len(summary["monthly_planned_breakdown"]), 12)
        self.assertEqual(breakdown[f"{self.today.year}/03"], Decimal("3000000"))
        self.assertEqual(breakdown[f"{self.today.year}/04"], Decimal("400000"))
        self.assertEqual(breakdown[f"{self.today.year}/05"], Decimal("400000"))
        self.assertEqual(breakdown[f"{self.today.year}/06"], Decimal("400000"))
        self.assertEqual(breakdown[f"{self.today.year}/01"], Decimal("0"))

    def test_fiscal_year_summary_respects_recent_ending(self):
        # customer A: a project ending this FY (qualifies for recent_ending) + a contract ending this FY
        ending = self._make_project("acme-ending", self.customer, date(self.today.year, 6, 30))
        self._make_contract(ending, "2000000", date(self.today.year, 6, 30))
        # customer B (empty_customer): its contract — and therefore its project, since the contract
        # period syncs onto the project dates — ends far in the future → excluded by recent_ending,
        # so its planned total drops out of the summary
        far = self._make_project("zeta-far", self.empty_customer, date(self.today.year + 2, 6, 30))
        self._make_contract(far, "5000000", date(self.today.year + 2, 6, 30))

        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/fiscal-year-summary/?recent_ending=true")
        summary = next(s for s in response.json() if s["organization"]["name"] == self.organization.name)
        self.assertEqual(summary["customer_count"], 1)  # only customer A is in scope
        self.assertEqual(Decimal(summary["planned_total"]), Decimal("2000000"))

    # --- (e) org-scoping -------------------------------------------------------------------------

    def test_other_org_customer_excluded_from_list_and_summary(self):
        self._make_project("globex-proj", self.other_customer, date(self.today.year, 6, 30))
        self.client.force_authenticate(user=self.user)
        list_ids = [r["id"] for r in self.client.get(f"{settings.URL_PREFIX}/api/customers/").json()["results"]]
        self.assertNotIn(str(self.other_customer.id), list_ids)
        summary = self.client.get(f"{settings.URL_PREFIX}/api/customers/fiscal-year-summary/").json()
        self.assertNotIn(self.other_organization.name, [s["organization"]["name"] for s in summary])

    def test_active_projects_other_org_customer_returns_404(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/{self.other_customer.id}/active-projects/")
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    # --- (f) auth --------------------------------------------------------------------------------

    def test_unauthenticated_list_rejected(self):
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/")
        self.assertIn(response.status_code, (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN))

    def test_unauthenticated_fiscal_year_summary_rejected(self):
        response = self.client.get(f"{settings.URL_PREFIX}/api/customers/fiscal-year-summary/")
        self.assertIn(response.status_code, (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN))
