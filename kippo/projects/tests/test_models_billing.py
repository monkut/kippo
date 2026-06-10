import datetime
from decimal import Decimal

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.db import IntegrityError
from django.test import TestCase

from projects.models import (
    BILLING_METHOD_DELIVERY,
    BILLING_METHOD_MONTHLY,
    DEFAULT_BILLING_METHOD,
    KippoProject,
    KippoProjectBillingEntry,
)


class BillingFieldsTestCase(TestCase):
    """Field-level tests for the kippo#31 / T11 billing fields."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]
        self.user = created["KippoUser"]

    def test_default_billing_method_is_delivery(self):
        # existing/new projects keep the single-point billing model by default
        self.assertEqual(DEFAULT_BILLING_METHOD, BILLING_METHOD_DELIVERY)
        self.assertEqual(self.project.billing_method, BILLING_METHOD_DELIVERY)

    def test_billing_fields_persist(self):
        self.project.billing_method = BILLING_METHOD_MONTHLY
        self.project.monthly_amount = Decimal("500000")
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.billing_method, BILLING_METHOD_MONTHLY)
        self.assertEqual(self.project.monthly_amount, Decimal("500000"))

    def test_monthly_amount_is_integer_jpy(self):
        # decimal_places=0 — JPY has no minor units
        self.assertEqual(KippoProject._meta.get_field("monthly_amount").decimal_places, 0)
        self.assertEqual(KippoProject._meta.get_field("monthly_amount").max_digits, 12)


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


class BillingEntryGenerationTestCase(TestCase):
    """Ledger generation tests for the kippo#31 / T12 monthly revenue accrual."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]
        self.user = created["KippoUser"]

    def _make_monthly(self, start: datetime.date, end: datetime.date, amount: str = "300000") -> None:
        # monthly accrual uses the project period (start_date/target_date)
        self.project.billing_method = BILLING_METHOD_MONTHLY
        self.project.monthly_amount = Decimal(amount)
        self.project.start_date = start
        self.project.target_date = end
        self.project.save()

    def test_multi_month_contract_generates_entry_per_month(self):
        self._make_monthly(datetime.date(2026, 1, 15), datetime.date(2026, 4, 10))
        created = self.project.generate_monthly_billing_entries(created_by=self.user)
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
        self.assertTrue(all(entry.created_by == self.user for entry in created))
        self.assertEqual(self.project.billing_entries.count(), 4)

    def test_single_month_contract(self):
        self._make_monthly(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))
        created = self.project.generate_monthly_billing_entries()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].billing_date, datetime.date(2026, 3, 1))
        self.assertEqual(created[0].amount, Decimal("300000"))

    def test_contract_spanning_year_boundary(self):
        self._make_monthly(datetime.date(2025, 11, 1), datetime.date(2026, 2, 28))
        created = self.project.generate_monthly_billing_entries()
        self.assertEqual(
            [entry.billing_date for entry in created],
            [
                datetime.date(2025, 11, 1),
                datetime.date(2025, 12, 1),
                datetime.date(2026, 1, 1),
                datetime.date(2026, 2, 1),
            ],
        )

    def test_generation_is_idempotent(self):
        self._make_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        first = self.project.generate_monthly_billing_entries()
        self.assertEqual(len(first), 3)
        second = self.project.generate_monthly_billing_entries()
        self.assertEqual(second, [])
        self.assertEqual(self.project.billing_entries.count(), 3)

    def test_generation_preserves_manual_adjustments(self):
        # a manually adjusted month (price revision / proration) must survive regeneration
        self._make_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        self.project.generate_monthly_billing_entries()
        adjusted = self.project.billing_entries.get(billing_date=datetime.date(2026, 2, 1))
        adjusted.amount = Decimal("150000")  # prorated month
        adjusted.save()

        self.project.target_date = datetime.date(2026, 4, 30)
        self.project.save()
        created = self.project.generate_monthly_billing_entries()
        # only the newly added month is created; the adjusted month is untouched
        self.assertEqual([entry.billing_date for entry in created], [datetime.date(2026, 4, 1)])
        adjusted.refresh_from_db()
        self.assertEqual(adjusted.amount, Decimal("150000"))

    def test_delivery_method_generates_nothing(self):
        # delivery projects record their single billing entry manually (no template generation)
        self.project.billing_method = BILLING_METHOD_DELIVERY
        self.project.monthly_amount = Decimal("300000")
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 6, 30)
        self.project.save()
        self.assertEqual(self.project.generate_monthly_billing_entries(), [])
        self.assertEqual(self.project.billing_entries.count(), 0)

    def test_monthly_without_required_fields_generates_nothing(self):
        self.project.billing_method = BILLING_METHOD_MONTHLY
        self.project.monthly_amount = None
        self.project.start_date = None
        self.project.target_date = None
        self.project.save()
        self.assertEqual(self.project.generate_monthly_billing_entries(), [])

    def test_duplicate_entry_for_same_month_rejected(self):
        # ledger uniqueness guard — one entry per (project, billing_date)
        self._make_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        self.project.generate_monthly_billing_entries()
        with self.assertRaises(IntegrityError):
            KippoProjectBillingEntry.objects.create(
                project=self.project,
                billing_date=datetime.date(2026, 1, 1),
                amount=Decimal("100"),
            )


class RevenueEntriesTestCase(TestCase):
    """Ledger query tests — the ledger is the single revenue source for both billing methods."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]

    def _generate_monthly(self, start: datetime.date, end: datetime.date, amount: str = "300000") -> None:
        self.project.billing_method = BILLING_METHOD_MONTHLY
        self.project.monthly_amount = Decimal(amount)
        self.project.start_date = start
        self.project.target_date = end
        self.project.save()
        self.project.generate_monthly_billing_entries()

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

    def test_delivery_entry_included_in_revenue(self):
        # delivery projects record a single ledger entry — included, no double counting
        KippoProjectBillingEntry.objects.create(
            project=self.project,
            billing_date=datetime.date(2026, 9, 30),
            amount=Decimal("2000000"),
            note="納品請求",
        )
        self.assertEqual(
            self.project.revenue_entries(),
            [(datetime.date(2026, 9, 30), Decimal("2000000"))],
        )

    def test_total_revenue_over_contract(self):
        self._generate_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 4, 30), amount="250000")
        total = sum(amount for _, amount in self.project.revenue_entries())
        self.assertEqual(total, Decimal("1000000"))
