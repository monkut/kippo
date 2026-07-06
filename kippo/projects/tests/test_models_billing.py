import datetime
from decimal import Decimal

from accounts.models import KippoUser
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from projects.definitions import (
    BILLING_TYPE_DELIVERY,
    BILLING_TYPE_MONTHLY,
    DEFAULT_BILLING_TYPE,
    DEFAULT_PRICING_BASIS,
    PRICING_BASIS_EFFORT,
    PRICING_BASIS_FIXED,
    ProjectRoles,
)
from projects.models import (
    KippoProject,
    KippoProjectBillingEntry,
    KippoProjectContract,
    ProjectAssignmentRate,
    ProjectMonthlyAssignment,
    ProjectWeeklyEffort,
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

    def test_contract_end_date_clearable_on_update_for_open_ended(self):
        # open-ended / retainer engagement (T&M): after creation the contract end_date can be cleared
        # and stays blank (not re-filled from the project), while the project keeps its planning
        # target_date (the sync never clears a project date).
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 6, 30)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("1800000"),
        )
        self.assertEqual(contract.end_date, datetime.date(2026, 6, 30))  # backfilled at creation
        contract.end_date = None
        contract.save()  # update: blank is honored, not re-filled
        contract.refresh_from_db()
        self.assertIsNone(contract.end_date)
        self.project.refresh_from_db()
        self.assertEqual(self.project.target_date, datetime.date(2026, 6, 30))  # planning date preserved

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

    def test_contract_period_synced_to_project_on_create(self):
        # once a contract exists its period is the single source of truth — the project's
        # start_date/target_date mirror it (KippoProjectContract._sync_project_period)
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 12, 31)
        self.project.save()
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("900000"),
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 5, 31),
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.start_date, datetime.date(2026, 3, 1))
        self.assertEqual(self.project.target_date, datetime.date(2026, 5, 31))

    def test_contract_period_synced_to_project_on_update(self):
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 6, 30)
        self.project.save()
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("1800000"),
        )
        contract.start_date = datetime.date(2026, 2, 1)
        contract.end_date = datetime.date(2026, 8, 31)
        contract.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.start_date, datetime.date(2026, 2, 1))
        self.assertEqual(self.project.target_date, datetime.date(2026, 8, 31))

    def test_period_sync_attributes_update_to_contract_editor(self):
        # a project-date change driven by a contract edit is attributed to the contract's editor
        # (updated_by), not the project's previous direct editor
        project_editor = self.user
        contract_editor = KippoUser.objects.get(username="github-manager")
        self.assertNotEqual(project_editor, contract_editor)
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 6, 30)
        self.project.updated_by = project_editor
        self.project.save()
        KippoProjectContract.objects.create(
            project=KippoProject.objects.get(pk=self.project.pk),
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("600000"),
            start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2026, 8, 31),
            created_by=contract_editor,
            updated_by=contract_editor,
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.target_date, datetime.date(2026, 8, 31))  # sync happened
        self.assertEqual(self.project.updated_by, contract_editor)  # attributed to the contract editor

    def test_blank_contract_period_does_not_clear_project_dates(self):
        # a contract date that stays blank (project had no dates to backfill from) must not
        # null-out anything on the project
        self.project.start_date = None
        self.project.target_date = None
        self.project.save()
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            total_amount=Decimal("500000"),
        )
        self.project.refresh_from_db()
        self.assertIsNone(self.project.start_date)
        self.assertIsNone(self.project.target_date)

    def test_period_sync_preserves_manual_confidence(self):
        # the sync-back is a partial save (update_fields) — it must not re-derive confidence
        self.project.start_date = datetime.date(2026, 1, 1)
        self.project.target_date = datetime.date(2026, 6, 30)
        self.project.save()
        KippoProject.objects.filter(pk=self.project.pk).update(confidence=55)
        contract = KippoProjectContract.objects.create(
            project=KippoProject.objects.get(pk=self.project.pk),
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("600000"),
        )
        contract.end_date = datetime.date(2026, 9, 30)
        contract.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.target_date, datetime.date(2026, 9, 30))
        self.assertEqual(self.project.confidence, 55)


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
            created_by=self.user,
            updated_by=self.user,
        )

    def test_billing_entries_generated_on_contract_creation(self):
        # creating the contract populates the ledger from the terms — the user does not run the
        # generate_billing_entries action by hand
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31), total_amount="900000")
        self.assertEqual(
            [(entry.billing_date, entry.amount) for entry in contract.billing_entries.all()],
            [
                (datetime.date(2026, 1, 31), Decimal("300000")),
                (datetime.date(2026, 2, 28), Decimal("300000")),
                (datetime.date(2026, 3, 31), Decimal("300000")),
            ],
        )
        self.assertTrue(all(entry.created_by == self.user for entry in contract.billing_entries.all()))  # contract's created_by
        self.assertTrue(all(entry.is_manual is False for entry in contract.billing_entries.all()))  # generated, not hand-added

    def test_multi_month_contract_generates_month_end_entry_per_month(self):
        # 1,200,000 over Jan..Apr (4 months) -> 300,000 each, generated on creation
        contract = self._make_contract(datetime.date(2026, 1, 15), datetime.date(2026, 4, 10), total_amount="1200000")
        created = list(contract.billing_entries.all())
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
        self.assertTrue(all(entry.is_manual is False for entry in created))  # generated, not hand-added
        self.assertTrue(all(entry.created_by == self.user for entry in created))
        self.assertEqual(contract.billing_entries.count(), 4)

    def test_total_amount_split_remainder_lands_on_final_month(self):
        # 1,000,000 over 3 months -> 333,333 / 333,333 / 333,334 (remainder on last); sums to total
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31), total_amount="1000000")
        created = list(contract.billing_entries.all())
        self.assertEqual(
            [entry.amount for entry in created],
            [Decimal("333333"), Decimal("333333"), Decimal("333334")],
        )
        self.assertEqual(sum(entry.amount for entry in created), Decimal("1000000"))

    def test_single_month_contract(self):
        contract = self._make_contract(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31), total_amount="300000")
        created = list(contract.billing_entries.all())
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].billing_date, datetime.date(2026, 3, 31))
        self.assertEqual(created[0].amount, Decimal("300000"))

    def test_contract_spanning_year_boundary(self):
        contract = self._make_contract(datetime.date(2025, 11, 1), datetime.date(2026, 2, 28))
        created = list(contract.billing_entries.all())
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
        created = list(contract.billing_entries.all())
        self.assertEqual(len(created), 3)

    def test_generation_is_idempotent(self):
        # entries are generated on contract creation; re-running the action creates nothing new
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        self.assertEqual(contract.billing_entries.count(), 3)
        self.assertEqual(contract.generate_billing_entries(), [])
        self.assertEqual(contract.billing_entries.count(), 3)

    def test_generation_preserves_manual_adjustments(self):
        # a manually adjusted month (price revision / proration) must survive regeneration
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        contract.generate_billing_entries()
        adjusted = contract.billing_entries.get(billing_date=datetime.date(2026, 2, 28))
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
        # ledger uniqueness guard — one entry per (contract, billing_date)
        contract = self._make_contract(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        contract.generate_billing_entries()
        with self.assertRaises(IntegrityError):
            KippoProjectBillingEntry.objects.create(
                contract=contract,
                is_manual=True,
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
        created = list(contract.billing_entries.all())
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
        created = list(contract.billing_entries.all())
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].billing_date, datetime.date(2026, 9, 30))

    def test_delivery_generation_is_idempotent(self):
        # the single delivery entry is generated on creation; re-running the action creates nothing new
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            total_amount=Decimal("2000000"),
            end_date=datetime.date(2026, 9, 30),
        )
        self.assertEqual(contract.billing_entries.count(), 1)
        self.assertEqual(contract.generate_billing_entries(), [])
        self.assertEqual(contract.billing_entries.count(), 1)


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
        # hand-added entries (is_manual) count toward revenue like generated ones.
        # effort pricing with no logged effort leaves the ledger empty on creation, so the manual
        # entry below is the only one.
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            pricing_basis=PRICING_BASIS_EFFORT,
            total_amount=None,
            end_date=datetime.date(2026, 9, 30),
        )
        KippoProjectBillingEntry.objects.create(
            contract=contract,
            billing_date=datetime.date(2026, 9, 30),
            amount=Decimal("500000"),
            is_manual=True,
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

    def test_contract_deletion_removes_billing_entries(self):
        # CASCADE: billing entries belong to the contract, so deleting it removes its ledger
        # (a separate engagement is a new project; contracts are amended, not deleted, in practice)
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            total_amount=Decimal("600000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 2, 28),
        )
        contract.generate_billing_entries()
        self.assertEqual(contract.billing_entries.count(), 2)
        contract.delete()
        self.assertEqual(KippoProjectBillingEntry.objects.count(), 0)


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
        entry = contract.billing_entries.get(billing_date=datetime.date(2026, 2, 28))
        entry.amount = Decimal("150000")  # prorated month
        entry.save()
        self.assertEqual(self.project.total_revenue, Decimal("750000"))  # 300k + 150k + 300k
        self.assertEqual(self.project.contract_amount, Decimal("900000"))  # unchanged: contract total_amount


class PricingBasisFieldTestCase(TestCase):
    """pricing_basis (fixed/effort) field, validation, and requires_estimate (effort-billing)."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]

    def test_default_pricing_basis_is_fixed(self):
        self.assertEqual(DEFAULT_PRICING_BASIS, PRICING_BASIS_FIXED)
        contract = KippoProjectContract.objects.create(project=self.project, total_amount=Decimal("1000000"))
        self.assertEqual(contract.pricing_basis, PRICING_BASIS_FIXED)

    def test_requires_estimate_true_for_fixed(self):
        contract = KippoProjectContract(project=self.project, pricing_basis=PRICING_BASIS_FIXED, total_amount=Decimal("1000000"))
        self.assertTrue(contract.requires_estimate)

    def test_requires_estimate_false_for_effort(self):
        contract = KippoProjectContract(project=self.project, pricing_basis=PRICING_BASIS_EFFORT)
        self.assertFalse(contract.requires_estimate)

    def test_clean_requires_total_amount_for_fixed(self):
        contract = KippoProjectContract(project=self.project, pricing_basis=PRICING_BASIS_FIXED, total_amount=None)
        with self.assertRaises(ValidationError):
            contract.clean()

    def test_clean_allows_blank_total_amount_for_effort(self):
        contract = KippoProjectContract(project=self.project, pricing_basis=PRICING_BASIS_EFFORT, total_amount=None)
        contract.clean()  # no raise

    def test_contract_amount_zero_when_effort_total_blank(self):
        KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            pricing_basis=PRICING_BASIS_EFFORT,
            total_amount=None,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 3, 31),
        )
        self.assertEqual(self.project.contract_amount, Decimal(0))


class EffortContractGenerationTestCase(TestCase):
    """Ledger generation for effort (T&M) contracts — amounts = Σ(hours ÷ day_workhours × role rate).

    setup_basic_project() builds an org with day_workhours=8 and 'octocat' as a developer member.
    """

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]
        self.user = created["KippoUser"]
        self.organization = created["KippoOrganization"]
        self.assertEqual(self.organization.day_workhours, 8)  # guards the test math below

    def _set_rate(self, role: str, rate_per_day: int) -> None:
        ProjectAssignmentRate.objects.create(project=self.project, role=role, rate_per_day=rate_per_day, created_by=self.user, updated_by=self.user)

    def _log_effort(self, week_start: datetime.date, hours: int) -> None:
        ProjectWeeklyEffort.objects.create(
            project=self.project, user=self.user, week_start=week_start, hours=hours, created_by=self.user, updated_by=self.user
        )

    def _assign_role(self, month: datetime.date, role: str) -> None:
        ProjectMonthlyAssignment.objects.create(
            project=self.project, user=self.user, month=month, role=role, percentage=100, created_by=self.user, updated_by=self.user
        )

    def _effort_contract(self, billing_type: str, start: datetime.date, end: datetime.date) -> KippoProjectContract:
        return KippoProjectContract.objects.create(
            project=self.project,
            billing_type=billing_type,
            pricing_basis=PRICING_BASIS_EFFORT,
            total_amount=None,
            start_date=start,
            end_date=end,
        )

    def test_effort_monthly_bills_logged_hours_per_month(self):
        self._set_rate(ProjectRoles.DEVELOPER.value, 100_000)
        self._log_effort(datetime.date(2026, 1, 5), 40)  # 5 days
        self._log_effort(datetime.date(2026, 1, 19), 8)  # 1 day  -> Jan total 6 days = 600,000
        self._log_effort(datetime.date(2026, 2, 2), 16)  # 2 days -> Feb 200,000
        contract = self._effort_contract(BILLING_TYPE_MONTHLY, datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
        created = list(contract.billing_entries.all())
        self.assertEqual(
            [(e.billing_date, e.amount) for e in created],
            [(datetime.date(2026, 1, 31), Decimal("600000")), (datetime.date(2026, 2, 28), Decimal("200000"))],
        )

    def test_effort_role_from_projectmonthlyassignment(self):
        # the tester rate applies because the month's ProjectMonthlyAssignment sets role=tester
        self._set_rate(ProjectRoles.DEVELOPER.value, 100_000)
        self._set_rate(ProjectRoles.TESTER.value, 50_000)
        self._assign_role(datetime.date(2026, 1, 1), ProjectRoles.TESTER.value)
        self._log_effort(datetime.date(2026, 1, 5), 40)  # 5 days × 50,000 = 250,000
        contract = self._effort_contract(BILLING_TYPE_MONTHLY, datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        created = list(contract.billing_entries.all())
        self.assertEqual([(e.billing_date, e.amount) for e in created], [(datetime.date(2026, 1, 31), Decimal("250000"))])

    def test_effort_missing_rate_falls_back_to_default(self):
        # no ProjectAssignmentRate rows -> developer role resolves to settings.DEFAULT_PROJECT_DAILY_RATE
        self._log_effort(datetime.date(2026, 1, 5), 40)  # 5 days × 180,000
        contract = self._effort_contract(BILLING_TYPE_MONTHLY, datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        created = list(contract.billing_entries.all())
        expected = Decimal(5 * settings.DEFAULT_PROJECT_DAILY_RATE)  # 900,000
        self.assertEqual([(e.billing_date, e.amount) for e in created], [(datetime.date(2026, 1, 31), expected)])

    def test_effort_delivery_settles_total_period_effort_at_end_date(self):
        self._set_rate(ProjectRoles.DEVELOPER.value, 100_000)
        self._log_effort(datetime.date(2026, 1, 5), 40)  # 5 days
        self._log_effort(datetime.date(2026, 2, 2), 16)  # 2 days -> total 7 days = 700,000
        contract = self._effort_contract(BILLING_TYPE_DELIVERY, datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
        created = list(contract.billing_entries.all())
        self.assertEqual([(e.billing_date, e.amount) for e in created], [(datetime.date(2026, 2, 28), Decimal("700000"))])

    def test_effort_monthly_zero_effort_month_creates_nothing(self):
        self._set_rate(ProjectRoles.DEVELOPER.value, 100_000)
        self._log_effort(datetime.date(2026, 1, 5), 40)  # Jan only
        self._log_effort(datetime.date(2026, 3, 2), 8)  # Mar only; Feb has no logged effort
        contract = self._effort_contract(BILLING_TYPE_MONTHLY, datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        created = list(contract.billing_entries.all())
        self.assertEqual(
            [(e.billing_date, e.amount) for e in created],
            [(datetime.date(2026, 1, 31), Decimal("500000")), (datetime.date(2026, 3, 31), Decimal("100000"))],
        )

    def test_effort_generation_is_idempotent(self):
        self._set_rate(ProjectRoles.DEVELOPER.value, 100_000)
        self._log_effort(datetime.date(2026, 1, 5), 40)
        contract = self._effort_contract(BILLING_TYPE_MONTHLY, datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        # the entry is generated on creation (effort already logged above); re-running creates nothing new
        self.assertEqual(contract.billing_entries.count(), 1)
        self.assertEqual(contract.generate_billing_entries(), [])
        self.assertEqual(contract.billing_entries.count(), 1)

    def test_effort_contract_has_empty_monthly_schedule(self):
        # the *planned* schedule is meaningless for effort (future hours unknown)
        contract = self._effort_contract(BILLING_TYPE_MONTHLY, datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        self.assertEqual(contract.monthly_schedule(), [])
        self.assertEqual(self.project.monthly_billing_schedule, [])


class EffortProvisionalBillingTestCase(TestCase):
    """Provisional (仮) monthly billing for effort contracts + true-up to actuals (実績) — kippo#46.

    setup_basic_project() builds an org with day_workhours=8 and 'octocat' as a developer member.
    """

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]
        self.user = created["KippoUser"]
        self.organization = created["KippoOrganization"]
        self.assertEqual(self.organization.day_workhours, 8)  # guards the test math below

    def _log_effort(self, week_start: datetime.date, hours: int) -> None:
        ProjectWeeklyEffort.objects.create(
            project=self.project, user=self.user, week_start=week_start, hours=hours, created_by=self.user, updated_by=self.user
        )

    def _provisional_contract(self, start: datetime.date, end: datetime.date, monthly_amount: int = 500_000) -> KippoProjectContract:
        return KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            pricing_basis=PRICING_BASIS_EFFORT,
            total_amount=None,
            estimated_monthly_amount=Decimal(monthly_amount),
            start_date=start,
            end_date=end,
        )

    def test_provisional_amount_bills_every_month_upfront(self):
        # no effort logged at all — every contract month (future included) still gets the 仮月額,
        # and a partial first month is NOT prorated
        contract = self._provisional_contract(datetime.date(2026, 1, 15), datetime.date(2026, 3, 31))
        self.assertEqual(
            [(e.billing_date, e.amount) for e in contract.billing_entries.all()],
            [
                (datetime.date(2026, 1, 31), Decimal("500000")),
                (datetime.date(2026, 2, 28), Decimal("500000")),
                (datetime.date(2026, 3, 31), Decimal("500000")),
            ],
        )

    def test_provisional_amount_overrides_logged_actuals_in_schedule(self):
        # provisional-first: logged effort does not change generated amounts — true-up does
        self._log_effort(datetime.date(2026, 1, 5), 40)
        contract = self._provisional_contract(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        self.assertEqual([e.amount for e in contract.billing_entries.all()], [Decimal("500000")])

    def test_planned_schedule_uses_provisional_amounts(self):
        contract = self._provisional_contract(datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
        self.assertEqual(
            contract.planned_billing_schedule(),
            [(datetime.date(2026, 1, 31), Decimal("500000")), (datetime.date(2026, 2, 28), Decimal("500000"))],
        )

    def test_clean_rejects_provisional_amount_for_fixed_pricing(self):
        contract = KippoProjectContract(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            pricing_basis=PRICING_BASIS_FIXED,
            total_amount=Decimal("1000000"),
            estimated_monthly_amount=Decimal("500000"),
        )
        with self.assertRaises(ValidationError):
            contract.clean()

    def test_clean_rejects_provisional_amount_for_effort_delivery(self):
        contract = KippoProjectContract(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            pricing_basis=PRICING_BASIS_EFFORT,
            estimated_monthly_amount=Decimal("500000"),
        )
        with self.assertRaises(ValidationError):
            contract.clean()

    def test_clean_allows_provisional_amount_for_effort_monthly(self):
        contract = KippoProjectContract(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            pricing_basis=PRICING_BASIS_EFFORT,
            estimated_monthly_amount=Decimal("500000"),
        )
        contract.clean()  # no raise

    def test_trueup_corrects_unreceived_entries_to_actuals(self):
        # Jan: 5 days logged -> 900,000 at the default rate; Feb: nothing logged -> 0 (entry kept)
        contract = self._provisional_contract(datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
        self._log_effort(datetime.date(2026, 1, 5), 40)
        updated_count = contract.trueup_billing_entries(updated_by=self.user)
        self.assertEqual(updated_count, 2)
        expected_january = Decimal(5 * settings.DEFAULT_PROJECT_DAILY_RATE)
        self.assertEqual(
            [(e.billing_date, e.amount) for e in contract.billing_entries.all()],
            [(datetime.date(2026, 1, 31), expected_january), (datetime.date(2026, 2, 28), Decimal("0"))],
        )

    def test_trueup_skips_received_entries(self):
        contract = self._provisional_contract(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        entry = contract.billing_entries.get()
        entry.is_received = True
        entry.save()
        self._log_effort(datetime.date(2026, 1, 5), 40)
        self.assertEqual(contract.trueup_billing_entries(updated_by=self.user), 0)
        entry.refresh_from_db()
        self.assertEqual(entry.amount, Decimal("500000"))  # received = final

    def test_trueup_stamps_updated_by(self):
        contract = self._provisional_contract(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        self._log_effort(datetime.date(2026, 1, 5), 40)
        contract.trueup_billing_entries(updated_by=self.user)
        self.assertEqual(contract.billing_entries.get().updated_by, self.user)

    def test_trueup_noop_for_fixed_contract(self):
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            pricing_basis=PRICING_BASIS_FIXED,
            total_amount=Decimal("900000"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 3, 31),
        )
        self.assertEqual(contract.trueup_billing_entries(updated_by=self.user), 0)
        self.assertEqual([e.amount for e in contract.billing_entries.all()], [Decimal("300000")] * 3)

    def test_blank_provisional_amount_keeps_actuals_generation(self):
        # backwards compatible: without 仮月額 the effort contract bills logged actuals directly
        self._log_effort(datetime.date(2026, 1, 5), 40)
        contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_MONTHLY,
            pricing_basis=PRICING_BASIS_EFFORT,
            total_amount=None,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 2, 28),
        )
        expected_january = Decimal(5 * settings.DEFAULT_PROJECT_DAILY_RATE)
        self.assertEqual(
            [(e.billing_date, e.amount) for e in contract.billing_entries.all()],
            [(datetime.date(2026, 1, 31), expected_january)],  # zero-effort Feb creates nothing
        )


class BillingEntryReceivedTrackingTestCase(TestCase):
    """is_received / received_datetime consistency on KippoProjectBillingEntry (mirrors
    KippoProject.is_closed / closed_datetime auto-management in save()).
    """

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.project: KippoProject = created["KippoProject"]
        self.user = created["KippoUser"]
        # effort pricing with no logged effort leaves the ledger empty on creation, so each test below
        # controls its own single entry via _entry() without colliding with an auto-generated one
        self.contract = KippoProjectContract.objects.create(
            project=self.project,
            billing_type=BILLING_TYPE_DELIVERY,
            pricing_basis=PRICING_BASIS_EFFORT,
            total_amount=None,
            end_date=datetime.date(2026, 9, 30),
        )

    def _entry(self, **kwargs) -> KippoProjectBillingEntry:
        defaults = {"contract": self.contract, "billing_date": datetime.date(2026, 9, 30), "amount": Decimal("1000000")}
        defaults.update(kwargs)
        return KippoProjectBillingEntry.objects.create(**defaults)

    def test_defaults_to_not_received(self):
        entry = self._entry()
        self.assertFalse(entry.is_received)
        self.assertIsNone(entry.received_datetime)

    def test_marking_received_autosets_datetime(self):
        entry = self._entry(is_received=True)
        self.assertTrue(entry.is_received)
        self.assertIsNotNone(entry.received_datetime)

    def test_explicit_received_datetime_preserved(self):
        when = timezone.now() - datetime.timedelta(days=3)
        entry = self._entry(is_received=True, received_datetime=when)
        entry.refresh_from_db()
        self.assertEqual(entry.received_datetime, when)

    def test_unmarking_received_clears_datetime(self):
        entry = self._entry(is_received=True)
        entry.is_received = False
        entry.save()
        entry.refresh_from_db()
        self.assertFalse(entry.is_received)
        self.assertIsNone(entry.received_datetime)

    def test_received_by_preserved_while_received(self):
        entry = self._entry(is_received=True, received_by=self.user)
        entry.refresh_from_db()
        self.assertEqual(entry.received_by, self.user)

    def test_unmarking_received_clears_received_by(self):
        entry = self._entry(is_received=True, received_by=self.user)
        entry.is_received = False
        entry.save()
        entry.refresh_from_db()
        self.assertIsNone(entry.received_by)
