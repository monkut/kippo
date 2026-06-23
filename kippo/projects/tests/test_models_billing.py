import datetime
from decimal import Decimal

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from projects.definitions import BILLING_TYPE_DELIVERY, BILLING_TYPE_MONTHLY, DEFAULT_BILLING_TYPE
from projects.models import (
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
        contract = KippoProjectContract.objects.create(project=self.project, total_amount=Decimal("1000000"))
        self.assertEqual(contract.billing_type, BILLING_TYPE_DELIVERY)

    def test_total_amount_is_integer_jpy(self):
        # decimal_places=0 — JPY has no minor units
        self.assertEqual(KippoProjectContract._meta.get_field("total_amount").decimal_places, 0)
        self.assertEqual(KippoProjectContract._meta.get_field("total_amount").max_digits, 12)

    def test_project_carries_no_billing_terms(self):
        # terms live on the contract — the project itself has no billing_type/monthly amount field
        kippoproject_fieldnames = {f.name for f in KippoProject._meta.get_fields()}
        self.assertNotIn("billing_method", kippoproject_fieldnames)
        self.assertNotIn("monthly_amount", kippoproject_fieldnames)

    def test_blank_period_auto_populated_from_project_on_save(self):
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 6, 30)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("1800000"),
        )
        contract.refresh_from_db()
        self.assertEqual(contract.start_date, datetime.date(2026, 1, 1))
        self.assertEqual(contract.end_date, datetime.date(2026, 6, 30))

    def test_explicit_contract_period_preserved_on_save(self):
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 12, 31)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("900000"),
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 5, 31),
        )
        contract.refresh_from_db()
        self.assertEqual(contract.start_date, datetime.date(2026, 3, 1))
        self.assertEqual(contract.end_date, datetime.date(2026, 5, 31))

    def test_clean_rejects_inverted_period(self):
        contract = KippoProjectContract(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("300000"),
            start_date=datetime.date(2026, 6, 1),
            end_date=datetime.date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            contract.clean()

    def test_project_allows_only_one_contract(self):
        # OneToOne — a separate engagement is a new project, not a second contract row
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("1800000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 6, 30),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            KippoProjectContract.objects.create(
                project=self.project,
                billing_type=BILLING_TYPE_MONTHLY,
                total_amount=Decimal("2100000"),
                start_date=datetime.date(2026, 7, 1),
                end_date=datetime.date(2026, 12, 31),
            )


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

    def _make_contract(self, start: datetime.date, end: datetime.date, total_amount: str = "300000") -> KippoProjectContract:
        return KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal(total_amount),
            start_date=start,
            end_date=end,
        )

    def test_multi_month_contract_generates_month_end_entry_per_month(self):
        # 1,200,000 over Jan..Apr (4 months) -> 300,000 each
        contract = self._make_contract(datetime.date(2026, 1, 15), datetime.date(2026, 4, 10), total_amount="1200000")
        created = contract.generate_billing_entries(created_by=self.user)
        # each accrual month is billed at month-end (月末)
        self.assertEqual(
            [entry.billing_date for entry in created],
            [
                datetime.date(2026, 1, 31),
                datetime.date(2026, 2, 28),
                datetime.date(2026, 3, 31),
                datetime.date(2026, 4, 30),
            ],
        )
        self.assertTrue(all(entry.amount == Decimal("300000") for entry in created))
        self.assertTrue(all(entry.contract == contract for entry in created))
        self.assertTrue(all(entry.created_by == self.user for entry in created))
        self.assertEqual(self.project.billing_entries.count(), 4)

    def test_total_amount_split_remainder_lands_on_final_month(self):
        # 1,000,000 over 3 months -> 333,333 / 333,333 / 333,334 (remainder on last); sums to total
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31), total_amount="1000000")
        created = contract.generate_billing_entries()
        self.assertEqual(
            [entry.amount for entry in created],
            [Decimal("333333"), Decimal("333333"), Decimal("333334")],
        )
        self.assertEqual(sum(entry.amount for entry in created), Decimal("1000000"))

    def test_single_month_contract(self):
        contract = self._make_contract(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31), total_amount="300000")
        created = contract.generate_billing_entries()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].billing_date, datetime.date(2026, 3, 31))
        self.assertEqual(created[0].amount, Decimal("300000"))

    def test_contract_spanning_year_boundary(self):
        contract = self._make_contract(datetime.date(2025, 11, 1), datetime.date(2026, 2, 28))
        created = contract.generate_billing_entries()
        self.assertEqual(
            [entry.billing_date for entry in created],
            [
                datetime.date(2025, 11, 30),
                datetime.date(2025, 12, 31),
                datetime.date(2026, 1, 31),
                datetime.date(2026, 2, 28),
            ],
        )

    def test_auto_populated_period_generates_from_project_dates(self):
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 3, 31)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("900000"),
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
        adjusted = self.project.billing_entries.get(billing_date=datetime.date(2026, 2, 28))
        adjusted.amount = Decimal("150000")  # prorated month
        adjusted.save()

        contract.end_date = datetime.date(2026, 4, 30)
        contract.save()
        created = contract.generate_billing_entries()
        # only the newly added month is created; the adjusted month is untouched
        self.assertEqual([entry.billing_date for entry in created], [datetime.date(2026, 4, 30)])
        adjusted.refresh_from_db()
        self.assertEqual(adjusted.amount, Decimal("150000"))

    def test_unresolvable_period_generates_nothing(self):
        self.project.start_date = None
        self.project.target_date = None
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("300000"),
        )
        self.assertEqual(contract.generate_billing_entries(), [])

    def test_duplicate_entry_for_same_date_rejected(self):
        # ledger uniqueness guard — one entry per (project, billing_date)
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        contract.generate_billing_entries()
        with self.assertRaises(IntegrityError):
            KippoProjectBillingEntry.objects.create(
                project=self.project,
                billing_date=datetime.date(2026, 1, 31),
                amount=Decimal("100"),
            )


class DeliveryContractGenerationTestCase(TestCase):
    """Ledger generation from delivery contracts — single entry at the contract end_date."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]

    def test_delivery_generates_single_entry_at_contract_end_date(self):
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            total_amount=Decimal("2000000"),
            end_date=datetime.date(2026, 9, 30),
        )
        created = contract.generate_billing_entries()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].billing_date, datetime.date(2026, 9, 30))
        self.assertEqual(created[0].amount, Decimal("2000000"))
        self.assertEqual(created[0].contract, contract)

    def test_delivery_end_date_auto_populates_from_project_target_date(self):
        # blank contract end_date is filled from project.target_date on save, then used as the bill date
        self.project.target_date = datetime.date(2026, 9, 30)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            total_amount=Decimal("2000000"),
        )
        created = contract.generate_billing_entries()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].billing_date, datetime.date(2026, 9, 30))

    def test_delivery_generation_is_idempotent(self):
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            total_amount=Decimal("2000000"),
            end_date=datetime.date(2026, 9, 30),
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

    def _generate_monthly(self, start: datetime.date, end: datetime.date, total_amount: str = "300000") -> None:
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal(total_amount),
            start_date=start,
            end_date=end,
        ).generate_billing_entries()

    def test_revenue_entries_returns_ledger(self):
        # 1,200,000 over 4 months -> 300,000 each
        self._generate_monthly(datetime.date(2026, 1, 15), datetime.date(2026, 4, 10), total_amount="1200000")
        entries = self.project.revenue_entries()
        self.assertEqual(
            entries,
            [
                (datetime.date(2026, 1, 31), Decimal("300000")),
                (datetime.date(2026, 2, 28), Decimal("300000")),
                (datetime.date(2026, 3, 31), Decimal("300000")),
                (datetime.date(2026, 4, 30), Decimal("300000")),
            ],
        )

    def test_window_clamps_to_requested_range(self):
        self._generate_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 6, 30), total_amount="1800000")
        months = [
            d
            for d, _ in self.project.revenue_entries(
                window_start=datetime.date(2026, 3, 10),
                window_end=datetime.date(2026, 5, 5),
            )
        ]
        self.assertEqual(
            months,
            [datetime.date(2026, 3, 31), datetime.date(2026, 4, 30), datetime.date(2026, 5, 31)],
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
        # the per-month split always sums back to total_amount
        self._generate_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 4, 30), total_amount="1000000")
        total = sum(amount for _, amount in self.project.revenue_entries())
        self.assertEqual(total, Decimal("1000000"))

    def test_contract_deletion_keeps_revenue_history(self):
        # SET_NULL: deleting a contract must not delete its generated revenue entries
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("600000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 2, 28),
        )
        contract.generate_billing_entries()
        contract.delete()
        self.assertEqual(self.project.billing_entries.count(), 2)
        self.assertTrue(all(entry.contract is None for entry in self.project.billing_entries.all()))


class DerivedRevenueFiguresTestCase(TestCase):
    """契約金額 / トータル売上 derived from the contract + ledger (kippo#32 / T13)."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]

    def test_delivery_contract_amount_is_total_amount(self):
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            total_amount=Decimal("2000000"),
            end_date=datetime.date(2026, 9, 30),
        )
        self.assertEqual(self.project.contract_amount, Decimal("2000000"))

    def test_monthly_contract_amount_is_total_amount(self):
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("1200000"),
            start_date=datetime.date(2026, 1, 15),
            end_date=datetime.date(2026, 4, 10),
        )
        self.assertEqual(self.project.contract_amount, Decimal("1200000"))

    def test_contract_amount_is_zero_without_contract(self):
        self.assertEqual(self.project.contract_amount, Decimal(0))

    def test_billing_types_reflects_contract(self):
        self.assertEqual(self.project.billing_types, [])
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("900000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 3, 31),
        )
        self.assertEqual(self.project.billing_types, [BILLING_TYPE_MONTHLY])

    def test_monthly_schedule_is_month_end_per_month(self):
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("900000"),
            start_date=datetime.date(2026, 1, 15),
            end_date=datetime.date(2026, 3, 10),
        )
        self.assertEqual(
            contract.monthly_schedule(),
            [
                (datetime.date(2026, 1, 31), Decimal("300000")),
                (datetime.date(2026, 2, 28), Decimal("300000")),
                (datetime.date(2026, 3, 31), Decimal("300000")),
            ],
        )

    def test_delivery_contract_has_empty_monthly_schedule(self):
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            total_amount=Decimal("2000000"),
            end_date=datetime.date(2026, 9, 30),
        )
        self.assertEqual(contract.monthly_schedule(), [])

    def test_project_monthly_billing_schedule_from_contract(self):
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("1100000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 4, 30),
        )
        # 1,100,000 over 4 months -> 275,000 each (clean split)
        self.assertEqual(
            self.project.monthly_billing_schedule,
            [
                (datetime.date(2026, 1, 31), Decimal("275000")),
                (datetime.date(2026, 2, 28), Decimal("275000")),
                (datetime.date(2026, 3, 31), Decimal("275000")),
                (datetime.date(2026, 4, 30), Decimal("275000")),
            ],
        )

    def test_project_monthly_billing_schedule_empty_for_delivery(self):
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            total_amount=Decimal("2000000"),
            end_date=datetime.date(2026, 9, 30),
        )
        self.assertEqual(self.project.monthly_billing_schedule, [])

    def test_total_revenue_sums_billing_ledger(self):
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("1000000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 4, 30),
        ).generate_billing_entries()
        # the 4 month-end entries sum back to total_amount
        self.assertEqual(self.project.total_revenue, Decimal("1000000"))

    def test_total_revenue_is_zero_without_entries(self):
        self.assertEqual(self.project.total_revenue, Decimal(0))

    def test_total_revenue_reflects_manual_adjustment(self):
        # total_revenue tracks the ledger, so an adjusted entry changes the total but not contract_amount
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("900000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 3, 31),
        )
        contract.generate_billing_entries()
        entry = self.project.billing_entries.get(billing_date=datetime.date(2026, 2, 28))
        entry.amount = Decimal("150000")  # prorated month
        entry.save()
        self.assertEqual(self.project.total_revenue, Decimal("750000"))  # 300k + 150k + 300k
        self.assertEqual(self.project.contract_amount, Decimal("900000"))  # unchanged: contract total_amount
