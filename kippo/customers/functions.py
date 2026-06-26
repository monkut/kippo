"""Reusable fiscal-year / summary / per-project computations for the customer changelist parity.

The single source of truth for the customer-changelist aggregates shared by ``KippoCustomerAdmin``
(which formats the raw values as ``¥``-strings) and ``KippoCustomerViewSet`` (which returns the raw
decimals/numbers for the UI to format). Keeping the math here avoids drift between the two surfaces.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from accounts.models import KippoOrganization
from django.db import models
from django.db.models import Count, IntegerField, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce
from projects.models import KippoProject, KippoProjectBillingEntry, KippoProjectContract

MONTHS_PER_YEAR = 12

# A customer's active (open + display_as_active) project count as a correlated scalar Subquery (NOT
# an aggregate), so it orders/annotates correctly on any queryset. Coalesce(..., 0) so customers
# with no active projects render 0 rather than NULL. (Mirrors admin.ACTIVE_PROJECT_COUNT.)
ACTIVE_PROJECT_COUNT = Coalesce(
    Subquery(
        KippoProject.objects.filter(customer=OuterRef("pk"), is_closed=False, display_as_active=True)
        .order_by()
        .values("customer")
        .annotate(count=Count("pk"))
        .values("count"),
        output_field=IntegerField(),
    ),
    0,
)


def shift_fiscal_year(fiscal_year_start: datetime.date, years: int) -> datetime.date:
    """The fiscal-year boundary ``years`` away from ``fiscal_year_start`` (same month, day 1)."""
    return datetime.date(fiscal_year_start.year + years, fiscal_year_start.month, 1)


def fiscal_year_months(fiscal_year_start: datetime.date) -> list[datetime.date]:
    """The 12 first-of-month dates of the fiscal year starting at ``fiscal_year_start``."""
    months = []
    for offset in range(MONTHS_PER_YEAR):
        month_index = (fiscal_year_start.month - 1 + offset) % MONTHS_PER_YEAR + 1
        year = fiscal_year_start.year + (fiscal_year_start.month - 1 + offset) // MONTHS_PER_YEAR
        months.append(datetime.date(year, month_index, 1))
    return months


def active_projects_contract_total(active_projects: list) -> Decimal:
    """Σ contract ``total_amount`` across the given active projects. Projects without a contract, or
    whose contract leaves ``total_amount`` blank (effort pricing), contribute 0.
    """
    return sum(
        (project.contract.total_amount for project in active_projects if getattr(project, "contract", None) and project.contract.total_amount),
        Decimal(0),
    )


def project_received_total_current_fy(project: KippoProject, fiscal_year_start: datetime.date) -> Decimal:
    """Σ a project's received billing-entry amounts billed on/after ``fiscal_year_start``.

    Expects the project's contract billing_entries to already be filtered to ``is_received=True``
    (the changelist prefetch does this); the fiscal-year cutoff is applied here per entry.
    """
    contract = getattr(project, "contract", None)
    if not contract:
        return Decimal(0)
    return sum((entry.amount for entry in contract.billing_entries.all() if entry.billing_date >= fiscal_year_start), Decimal(0))


def monthly_planned_breakdown(customer_pks: list, fiscal_year_start: datetime.date, fiscal_year_end: datetime.date) -> dict[datetime.date, Decimal]:
    """Per-FY-month planned contract totals across ``customer_pks`` contracts, keyed by first-of-month.

    Each contract's planned billing schedule (KippoProjectContract.planned_billing_schedule) is
    bucketed into the FY month its billing_date falls in. Contracts billing outside the FY contribute
    nothing. Every FY month is present (0 when nothing bills that month).
    """
    months = fiscal_year_months(fiscal_year_start)
    monthly_totals: dict[datetime.date, Decimal] = {month: Decimal(0) for month in months}
    # Only contracts that can bill on/after the FY start are relevant; bounds the per-contract schedule work.
    contracts = (
        KippoProjectContract.objects.filter(project__customer__in=customer_pks, end_date__gte=fiscal_year_start)
        .select_related("project__organization")
        .prefetch_related("project__assignment_rates")
    )
    for contract in contracts:
        for billing_date, amount in contract.planned_billing_schedule():
            if amount and fiscal_year_start <= billing_date < fiscal_year_end:
                monthly_totals[datetime.date(billing_date.year, billing_date.month, 1)] += amount
    return monthly_totals


def fiscal_year_org_summaries(customers: models.QuerySet) -> list[dict]:
    """Per-organization current-fiscal-year summary scoped to the (filtered) ``customers``, with RAW
    decimal/number values (no ``¥`` formatting). Per org: customer count; contracts whose end_date
    falls in the current FY (count + planned total); received total; month-by-month planned breakdown.
    Sorted by organization name.

    The shared source of truth — ``KippoCustomerAdmin`` formats these raw values; the API returns them.
    """
    customer_pks_by_org: dict = defaultdict(list)
    for customer_pk, organization_id in customers.values_list("pk", "organization_id"):
        customer_pks_by_org[organization_id].append(customer_pk)

    organizations = {org.pk: org for org in KippoOrganization.objects.filter(pk__in=customer_pks_by_org)}
    summaries = []
    for organization_id, customer_pks in customer_pks_by_org.items():
        organization = organizations[organization_id]
        fiscal_year_start = organization.current_fiscal_year_start()
        fiscal_year_end = shift_fiscal_year(fiscal_year_start, 1)
        contracts = KippoProjectContract.objects.filter(
            project__customer__in=customer_pks,
            end_date__gte=fiscal_year_start,
            end_date__lt=fiscal_year_end,
        )
        contract_summary = contracts.aggregate(count=Count("pk"), total=Sum("total_amount"))
        received_total = KippoProjectBillingEntry.objects.filter(contract__in=contracts, is_received=True).aggregate(total=Sum("amount"))[
            "total"
        ] or Decimal(0)
        breakdown = monthly_planned_breakdown(customer_pks, fiscal_year_start, fiscal_year_end)
        summaries.append(
            {
                "organization": organization,
                "fiscal_year_start": fiscal_year_start,
                "fiscal_year_end": fiscal_year_end,
                "customer_count": len(customer_pks),
                "project_count": contract_summary["count"],
                "planned_total": contract_summary["total"] or Decimal(0),
                "received_total": received_total,
                "monthly_planned_breakdown": [{"month": month.strftime("%Y/%m"), "amount": amount} for month, amount in breakdown.items()],
            }
        )
    return sorted(summaries, key=lambda summary: summary["organization"].name)
