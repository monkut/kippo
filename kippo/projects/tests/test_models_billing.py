import datetime
from decimal import Decimal

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from projects.models import (
    BILLING_TYPE_DELIVERY,
    BILLING_TYPE_MONTHLY,
    DEFAULT_BILLING_TYPE,
    KippoProject,
    KippoProjectBillingEntry,
    KippoProjectContract,
)


class ContractFieldsTestCase(TestCase):
    """Field-level tests for the kippo#31 / T11 contract terms."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]
        self.user = created["KippoUser"]

    def test_default_billing_type_is_delivery(self):
        self.assertEqual(DEFAULT_BILLING_TYPE, BILLING_TYPE_DELIVERY)
        contract = KippoProjectContract.objects.create(project=self.project, amount=Decimal("1000000"))
        self.assertEqual(contract.billing_type, BILLING_TYPE_DELIVERY)

    def test_amount_is_integer_jpy(self):
        # decimal_places=0 — JPY has no minor units
        self.assertEqual(KippoProjectContract._meta.get_field("amount").decimal_places, 0)
        self.assertEqual(KippoProjectContract._meta.get_field("amount").max_digits, 12)

    def test_project_carries_no_billing_terms(self):
        # terms live on the contract — the project itself has no billing_type/monthly amount field
        kippoproject_fieldnames = {f.name for f in KippoProject._meta.get_fields()}
        self.assertNotIn("billing_method", kippoproject_fieldnames)
        self.assertNotIn("monthly_amount", kippoproject_fieldnames)

    def test_period_falls_back_to_project_period(self):
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 6, 30)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal("300000"),
        )
        self.assertEqual(contract.period, (datetime.date(2026, 1, 1), datetime.date(2026, 6, 30)))

    def test_explicit_contract_period_overrides_project_period(self):
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 12, 31)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal("300000"),
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 5, 31),
        )
        self.assertEqual(contract.period, (datetime.date(2026, 3, 1), datetime.date(2026, 5, 31)))

    def test_clean_rejects_inverted_period(self):
        contract = KippoProjectContract(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal("300000"),
            start_date=datetime.date(2026, 6, 1),
            end_date=datetime.date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            contract.clean()

    def test_renewal_is_an_additional_contract_row(self):
        # renewals/amendments are new rows, not overwrites
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal("300000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 6, 30),
        )
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal("350000"),
            start_date=datetime.date(2026, 7, 1),
            end_date=datetime.date(2026, 12, 31),
        )
        self.assertEqual(self.project.contracts.count(), 2)


class BillingDateConsistencyTestCase(TestCase):
    """billing_date default behaviour must remain intact (kippo#31 keep-consistent guard)."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]

    def test_billing_date_defaults_to_target_date(self):
        self.project.billing_date = None
        self.project.target_date = datetime.date(2026, 9, 30)
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.billing_date, datetime.date(2026, 9, 30))

    def test_explicit_billing_date_preserved(self):
        self.project.billing_date = datetime.date(2026, 12, 25)
        self.project.target_date = datetime.date(2026, 9, 30)
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.billing_date, datetime.date(2026, 12, 25))


class MonthlyContractGenerationTestCase(TestCase):
    """Ledger generation from monthly contracts (kippo#31 / T12)."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]
        self.user = created["KippoUser"]

    def _make_contract(self, start: datetime.date, end: datetime.date, amount: str = "300000") -> KippoProjectContract:
        return KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal(amount),
            start_date=start,
            end_date=end,
        )

    def test_multi_month_contract_generates_entry_per_month(self):
        contract = self._make_contract(datetime.date(2026, 1, 15), datetime.date(2026, 4, 10))
        created = contract.generate_billing_entries(created_by=self.user)
        self.assertEqual(
            [entry.billing_date for entry in created],
            [
                datetime.date(2026, 1, 1),
                datetime.date(2026, 2, 1),
                datetime.date(2026, 3, 1),
                datetime.date(2026, 4, 1),
            ],
        )
        self.assertTrue(all(entry.amount == Decimal("300000") for entry in created))
        self.assertTrue(all(entry.contract == contract for entry in created))
        self.assertTrue(all(entry.created_by == self.user for entry in created))
        self.assertEqual(self.project.billing_entries.count(), 4)

    def test_single_month_contract(self):
        contract = self._make_contract(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))
        created = contract.generate_billing_entries()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].billing_date, datetime.date(2026, 3, 1))
        self.assertEqual(created[0].amount, Decimal("300000"))

    def test_contract_spanning_year_boundary(self):
        contract = self._make_contract(datetime.date(2025, 11, 1), datetime.date(2026, 2, 28))
        created = contract.generate_billing_entries()
        self.assertEqual(
            [entry.billing_date for entry in created],
            [
                datetime.date(2025, 11, 1),
                datetime.date(2025, 12, 1),
                datetime.date(2026, 1, 1),
                datetime.date(2026, 2, 1),
            ],
        )

    def test_period_falls_back_to_project_period(self):
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 3, 31)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal("300000"),
        )
        created = contract.generate_billing_entries()
        self.assertEqual(len(created), 3)

    def test_generation_is_idempotent(self):
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        first = contract.generate_billing_entries()
        self.assertEqual(len(first), 3)
        second = contract.generate_billing_entries()
        self.assertEqual(second, [])
        self.assertEqual(self.project.billing_entries.count(), 3)

    def test_generation_preserves_manual_adjustments(self):
        # a manually adjusted month (price revision / proration) must survive regeneration
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        contract.generate_billing_entries()
        adjusted = self.project.billing_entries.get(billing_date=datetime.date(2026, 2, 1))
        adjusted.amount = Decimal("150000")  # prorated month
        adjusted.save()

        contract.end_date = datetime.date(2026, 4, 30)
        contract.save()
        created = contract.generate_billing_entries()
        # only the newly added month is created; the adjusted month is untouched
        self.assertEqual([entry.billing_date for entry in created], [datetime.date(2026, 4, 1)])
        adjusted.refresh_from_db()
        self.assertEqual(adjusted.amount, Decimal("150000"))

    def test_unresolvable_period_generates_nothing(self):
        self.project.start_date = None
        self.project.target_date = None
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal("300000"),
        )
        self.assertEqual(contract.generate_billing_entries(), [])

    def test_duplicate_entry_for_same_date_rejected(self):
        # ledger uniqueness guard — one entry per (project, billing_date)
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        contract.generate_billing_entries()
        with self.assertRaises(IntegrityError):
            KippoProjectBillingEntry.objects.create(
                project=self.project,
                billing_date=datetime.date(2026, 1, 1),
                amount=Decimal("100"),
            )


class DeliveryContractGenerationTestCase(TestCase):
    """Ledger generation from delivery contracts — single entry at the billing date."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]

    def test_delivery_generates_single_entry_at_contract_billing_date(self):
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            amount=Decimal("2000000"),
            billing_date=datetime.date(2026, 9, 30),
        )
        created = contract.generate_billing_entries()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].billing_date, datetime.date(2026, 9, 30))
        self.assertEqual(created[0].amount, Decimal("2000000"))
        self.assertEqual(created[0].contract, contract)

    def test_delivery_falls_back_to_project_billing_date(self):
        # project.billing_date defaults to target_date on save
        self.project.billing_date = None
        self.project.target_date = datetime.date(2026, 9, 30)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            amount=Decimal("2000000"),
        )
        created = contract.generate_billing_entries()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].billing_date, datetime.date(2026, 9, 30))

    def test_delivery_generation_is_idempotent(self):
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            amount=Decimal("2000000"),
            billing_date=datetime.date(2026, 9, 30),
        )
        self.assertEqual(len(contract.generate_billing_entries()), 1)
        self.assertEqual(contract.generate_billing_entries(), [])
        self.assertEqual(self.project.billing_entries.count(), 1)


class RevenueEntriesTestCase(TestCase):
    """Ledger query tests — the ledger is the single revenue source for both billing types."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]

    def _generate_monthly(self, start: datetime.date, end: datetime.date, amount: str = "300000") -> None:
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal(amount),
            start_date=start,
            end_date=end,
        ).generate_billing_entries()

    def test_revenue_entries_returns_ledger(self):
        self._generate_monthly(datetime.date(2026, 1, 15), datetime.date(2026, 4, 10))
        entries = self.project.revenue_entries()
        self.assertEqual(
            entries,
            [
                (datetime.date(2026, 1, 1), Decimal("300000")),
                (datetime.date(2026, 2, 1), Decimal("300000")),
                (datetime.date(2026, 3, 1), Decimal("300000")),
                (datetime.date(2026, 4, 1), Decimal("300000")),
            ],
        )

    def test_window_clamps_to_requested_range(self):
        self._generate_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 6, 30))
        months = [
            d
            for d, _ in self.project.revenue_entries(
                window_start=datetime.date(2026, 3, 10),
                window_end=datetime.date(2026, 5, 5),
            )
        ]
        self.assertEqual(
            months,
            [datetime.date(2026, 3, 1), datetime.date(2026, 4, 1), datetime.date(2026, 5, 1)],
        )

    def test_window_outside_contract_yields_nothing(self):
        self._generate_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        entries = self.project.revenue_entries(
            window_start=datetime.date(2026, 6, 1),
            window_end=datetime.date(2026, 12, 31),
        )
        self.assertEqual(entries, [])

    def test_manual_entry_included_in_revenue(self):
        # entries added by hand (no contract) count toward revenue like generated ones
        KippoProjectBillingEntry.objects.create(
            project=self.project,
            billing_date=datetime.date(2026, 9, 30),
            amount=Decimal("500000"),
            note="追加請求",
        )
        self.assertEqual(
            self.project.revenue_entries(),
            [(datetime.date(2026, 9, 30), Decimal("500000"))],
        )

    def test_total_revenue_over_contract(self):
        self._generate_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 4, 30), amount="250000")
        total = sum(amount for _, amount in self.project.revenue_entries())
        self.assertEqual(total, Decimal("1000000"))

    def test_contract_deletion_keeps_revenue_history(self):
        # SET_NULL: deleting a contract must not delete its generated revenue entries
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            amount=Decimal("300000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 2, 28),
        )
        contract.generate_billing_entries()
        contract.delete()
        self.assertEqual(self.project.billing_entries.count(), 2)
        self.assertTrue(all(entry.contract is None for entry in self.project.billing_entries.all()))
