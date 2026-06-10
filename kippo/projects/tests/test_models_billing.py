import datetime
from decimal import Decimal

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.core.exceptions import ValidationError
from django.test import TestCase

from projects.models import (
    BILLING_METHOD_DELIVERY,
    BILLING_METHOD_MONTHLY,
    DEFAULT_BILLING_METHOD,
    KippoProject,
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
        self.project.contract_start_date = datetime.date(2026, 1, 1)
        self.project.contract_end_date = datetime.date(2026, 6, 30)
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.billing_method, BILLING_METHOD_MONTHLY)
        self.assertEqual(self.project.monthly_amount, Decimal("500000"))
        self.assertEqual(self.project.contract_start_date, datetime.date(2026, 1, 1))
        self.assertEqual(self.project.contract_end_date, datetime.date(2026, 6, 30))

    def test_monthly_amount_is_integer_jpy(self):
        # decimal_places=0 — JPY has no minor units
        self.assertEqual(KippoProject._meta.get_field("monthly_amount").decimal_places, 0)
        self.assertEqual(KippoProject._meta.get_field("monthly_amount").max_digits, 12)

    def test_clean_rejects_contract_start_after_end(self):
        self.project.contract_start_date = datetime.date(2026, 6, 1)
        self.project.contract_end_date = datetime.date(2026, 1, 1)
        with self.assertRaises(ValidationError):
            self.project.clean()

    def test_clean_allows_single_month_contract(self):
        self.project.contract_start_date = datetime.date(2026, 3, 1)
        self.project.contract_end_date = datetime.date(2026, 3, 31)
        # should not raise
        self.project.clean()


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


class MonthlyRevenueEntriesTestCase(TestCase):
    """Aggregation tests for the kippo#31 / T12 monthly revenue accrual."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]

    def _make_monthly(self, start: datetime.date, end: datetime.date, amount: str = "300000") -> None:
        self.project.billing_method = BILLING_METHOD_MONTHLY
        self.project.monthly_amount = Decimal(amount)
        self.project.contract_start_date = start
        self.project.contract_end_date = end
        self.project.save()

    def test_multi_month_contract_accrues_each_month(self):
        self._make_monthly(datetime.date(2026, 1, 15), datetime.date(2026, 4, 10))
        entries = self.project.monthly_revenue_entries()
        self.assertEqual(
            [d for d, _ in entries],
            [
                datetime.date(2026, 1, 1),
                datetime.date(2026, 2, 1),
                datetime.date(2026, 3, 1),
                datetime.date(2026, 4, 1),
            ],
        )
        self.assertTrue(all(amount == Decimal("300000") for _, amount in entries))

    def test_single_month_contract(self):
        self._make_monthly(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))
        entries = self.project.monthly_revenue_entries()
        self.assertEqual(entries, [(datetime.date(2026, 3, 1), Decimal("300000"))])

    def test_contract_spanning_year_boundary(self):
        self._make_monthly(datetime.date(2025, 11, 1), datetime.date(2026, 2, 28))
        months = [d for d, _ in self.project.monthly_revenue_entries()]
        self.assertEqual(
            months,
            [
                datetime.date(2025, 11, 1),
                datetime.date(2025, 12, 1),
                datetime.date(2026, 1, 1),
                datetime.date(2026, 2, 1),
            ],
        )

    def test_window_clamps_to_requested_range(self):
        self._make_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 6, 30))
        months = [
            d
            for d, _ in self.project.monthly_revenue_entries(
                window_start=datetime.date(2026, 3, 10),
                window_end=datetime.date(2026, 5, 5),
            )
        ]
        self.assertEqual(
            months,
            [datetime.date(2026, 3, 1), datetime.date(2026, 4, 1), datetime.date(2026, 5, 1)],
        )

    def test_window_outside_contract_yields_nothing(self):
        self._make_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        entries = self.project.monthly_revenue_entries(
            window_start=datetime.date(2026, 6, 1),
            window_end=datetime.date(2026, 12, 31),
        )
        self.assertEqual(entries, [])

    def test_delivery_method_excluded_from_monthly_accrual(self):
        # delivery projects must NOT accrue monthly revenue (no double counting)
        self.project.billing_method = BILLING_METHOD_DELIVERY
        self.project.monthly_amount = Decimal("300000")
        self.project.contract_start_date = datetime.date(2026, 1, 1)
        self.project.contract_end_date = datetime.date(2026, 6, 30)
        self.project.save()
        self.assertEqual(self.project.monthly_revenue_entries(), [])

    def test_monthly_without_required_fields_yields_nothing(self):
        self.project.billing_method = BILLING_METHOD_MONTHLY
        self.project.monthly_amount = None
        self.project.contract_start_date = None
        self.project.contract_end_date = None
        self.project.save()
        self.assertEqual(self.project.monthly_revenue_entries(), [])

    def test_total_revenue_over_contract(self):
        self._make_monthly(datetime.date(2026, 1, 1), datetime.date(2026, 4, 30), amount="250000")
        total = sum(amount for _, amount in self.project.monthly_revenue_entries())
        self.assertEqual(total, Decimal("1000000"))
