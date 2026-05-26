"""Load ProjectMonthlyAssignment rows from a Google Sheet.

Reads a sheet whose columns encode per-user workload percentages for projects in
a single month, and upserts matching `ProjectMonthlyAssignment` rows scoped to a
KippoOrganization.

The mapping file (Kanji column → KippoUser, sheet alias → KippoCustomer,
sheet alias → KippoProject) is supplied locally via ``--mapping`` and MUST NOT
be committed; it contains org-specific people data.

Sheet layout (rows are 1-indexed):
    row 1: title row (ignored)
    row 2: header — col A blank, B=No., C=顧客, D=プロジェクト内容, E=売上,
           F.. = per-user single-Kanji column code
    row 3: per-user availability % (ignored — header metadata)
    row 4+: data — col A '確' = confirmed, C/D customer+project, F+ workload %

Project cell text supports three forms (loader recognises all three):
    "<description> (<KippoProject canonical name>)"  → uses parenthesized hint
    "<KippoProject canonical name>"                  → used directly
    "<description>"                                  → must be in project_aliases

Fetching the sheet:
    Default path uses HTTP against the Sheets API; export an OAuth2 access token
    in ``GOOGLE_SHEETS_OAUTH_TOKEN`` first (e.g. via ``gws auth print-token``
    or ``gcloud auth print-access-token``). For repeat runs or testing, pre-export
    the sheet to JSON and pass ``--input-json <path>``.

Example:
    GOOGLE_SHEETS_OAUTH_TOKEN=$(gws auth print-token) \
    uv run python manage.py load_monthly_assignments_from_sheet \
        --spreadsheet-id 16TVnGPMse7AHQT1oX-aqRDW9y3UIaZadstH36TOVAxk \
        --sheet 202606 \
        --organization KiconiaWorks \
        --mapping ~/.kippo/monthly_assignment_mapping.json \
        --dry-run
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
from accounts.models import KippoOrganization, KippoUser
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from projects.models import KippoCustomer, KippoProject, ProjectMonthlyAssignment

if TYPE_CHECKING:
    from argparse import ArgumentParser

logger = logging.getLogger(__name__)

SHEET_API_URL_TEMPLATE = "https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range}"
SHEET_RANGE_TEMPLATE = "{sheet}!A1:AB1000"
SHEET_NAME_DATE_FORMAT = "%Y%m"
CONFIRMED_MARKER = "確"
HEADER_ROW_COUNT = 3  # rows 1-3 are headers; data starts at row 4
USER_COLUMN_START_INDEX = 5  # col F (0-indexed)
CUSTOMER_COL_INDEX = 2
PROJECT_COL_INDEX = 3
CONFIRMED_COL_INDEX = 0


@dataclass
class LoadStats:
    matched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_cells: int = 0
    unresolved_customer: list[str] = field(default_factory=list)
    unresolved_project: list[str] = field(default_factory=list)
    unresolved_user: list[str] = field(default_factory=list)


@dataclass
class Mapping:
    user_columns: dict[str, str]  # kanji → KippoUser.username
    customer_aliases: dict[str, str | None]  # sheet 顧客 → KippoCustomer.name (or None to skip customer scope)
    project_aliases: dict[str, str]  # "顧客|project_text" → KippoProject.name


def _load_mapping(path: Path) -> Mapping:
    if not path.exists():
        raise CommandError(f"Mapping file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "user_columns" not in raw or not isinstance(raw["user_columns"], dict):
        raise CommandError("Mapping file must contain a 'user_columns' object")
    return Mapping(
        user_columns=raw["user_columns"],
        customer_aliases=raw.get("customer_aliases", {}) or {},
        project_aliases=raw.get("project_aliases", {}) or {},
    )


def _fetch_sheet_values(spreadsheet_id: str, sheet: str, oauth_token: str) -> list[list[str]]:
    url = SHEET_API_URL_TEMPLATE.format(
        spreadsheet_id=spreadsheet_id,
        range=SHEET_RANGE_TEMPLATE.format(sheet=sheet),
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {oauth_token}"}, timeout=30)
    if resp.status_code != requests.codes.ok:
        raise CommandError(f"Sheets API error {resp.status_code}: {resp.text}")
    return resp.json().get("values", []) or []


def _parse_month_from_sheet_name(sheet: str) -> datetime.date:
    try:
        return datetime.datetime.strptime(sheet, SHEET_NAME_DATE_FORMAT).replace(day=1).date()  # noqa: DTZ007
    except ValueError as exc:
        raise CommandError(f"--sheet must be in YYYYMM format, got: {sheet}") from exc


def _extract_project_name_hint(cell_text: str) -> str | None:
    """Pull a parenthesized KippoProject canonical name out of a project cell, if present.

    Sheet authors mark explicit mappings as ``<description> (<KippoProject name>)``;
    this returns the inner name or None.
    """
    stripped = cell_text.strip()
    if stripped.endswith(")") and "(" in stripped:
        open_idx = stripped.rfind("(")
        return stripped[open_idx + 1 : -1].strip()
    return None


def _parse_percentage(raw: str) -> int | None:
    """Parse a sheet cell into a percentage integer; returns None for blank / non-numeric."""
    value = (raw or "").strip().rstrip("%").strip()
    if not value:
        return None
    try:
        return int(round(float(value)))
    except ValueError:
        return None


def _resolve_user_columns(header_row: list[str], mapping: Mapping) -> dict[int, KippoUser]:
    """Return {column_index: KippoUser} for every mapped user column present in the sheet."""
    resolved: dict[int, KippoUser] = {}
    unresolved: list[tuple[int, str]] = []
    for col_idx in range(USER_COLUMN_START_INDEX, len(header_row)):
        kanji = (header_row[col_idx] or "").strip()
        if not kanji:
            continue
        username = mapping.user_columns.get(kanji)
        if not username:
            unresolved.append((col_idx, kanji))
            continue
        try:
            resolved[col_idx] = KippoUser.objects.get(username=username)
        except KippoUser.DoesNotExist:
            unresolved.append((col_idx, f"{kanji} → {username}"))
    if unresolved:
        for col_idx, label in unresolved:
            logger.warning("user column %s (%s) not resolvable", col_idx, label)
    return resolved


def _resolve_project(
    raw_customer: str,
    raw_project: str,
    mapping: Mapping,
    organization: KippoOrganization,
) -> KippoProject | None:
    """Resolve a (顧客, project_text) sheet pair to a KippoProject within `organization`.

    Lookup precedence:
    1. project_aliases by "raw_customer|raw_project" key
    2. parenthesized hint inside the project cell text
    3. bare project cell text
    """
    raw_customer = raw_customer.strip()
    raw_project = raw_project.strip()
    alias_key = f"{raw_customer}|{raw_project}"
    candidate_name = mapping.project_aliases.get(alias_key) or _extract_project_name_hint(raw_project) or raw_project
    if not candidate_name:
        return None
    try:
        return KippoProject.objects.get(organization=organization, name=candidate_name)
    except KippoProject.DoesNotExist:
        return None


def _resolve_customer(raw_customer: str, mapping: Mapping, organization: KippoOrganization) -> KippoCustomer | None:
    """Resolve sheet 顧客 to a KippoCustomer; returns None if the alias maps to None (no customer scope)."""
    raw_customer = raw_customer.strip()
    if raw_customer in mapping.customer_aliases:
        aliased = mapping.customer_aliases[raw_customer]
        if aliased is None:
            return None
        raw_customer = aliased
    return KippoCustomer.objects.filter(organization=organization, name=raw_customer).first()


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--spreadsheet-id",
            default="16TVnGPMse7AHQT1oX-aqRDW9y3UIaZadstH36TOVAxk",
            help="Google Sheets spreadsheet ID (default: 第9期 アサイン表)",
        )
        parser.add_argument(
            "--sheet",
            required=True,
            help="Sheet tab name in YYYYMM format (e.g. 202606)",
        )
        parser.add_argument(
            "--organization",
            default="KiconiaWorks",
            help="KippoOrganization.name to scope all lookups to",
        )
        parser.add_argument(
            "--mapping",
            required=True,
            type=Path,
            help="Path to local mapping JSON (NOT committed; see module docstring for schema)",
        )
        parser.add_argument(
            "--input-json",
            type=Path,
            default=None,
            help="Optional: load pre-exported sheet values from a JSON file instead of fetching via API",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse + resolve only; do not write to the database",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401, ARG002
        sheet: str = options["sheet"]
        organization_name: str = options["organization"]
        dry_run: bool = options["dry_run"]

        try:
            organization = KippoOrganization.objects.get(name=organization_name)
        except KippoOrganization.DoesNotExist as exc:
            raise CommandError(f"KippoOrganization not found: {organization_name}") from exc

        try:
            cli_user = KippoUser.objects.get(username=settings.CLI_MANAGER_USERNAME)
        except KippoUser.DoesNotExist as exc:
            raise CommandError(f"CLI_MANAGER_USERNAME user not found: {settings.CLI_MANAGER_USERNAME}") from exc

        mapping = _load_mapping(options["mapping"])
        month = _parse_month_from_sheet_name(sheet)

        rows = self._load_rows(options, sheet)
        if len(rows) <= HEADER_ROW_COUNT:
            self.stdout.write(self.style.WARNING(f"sheet {sheet} has no data rows"))
            return

        user_columns = _resolve_user_columns(rows[1], mapping)
        if not user_columns:
            raise CommandError("no user columns resolved from sheet header — check --mapping user_columns")

        stats = LoadStats()
        with transaction.atomic():
            for row_idx, raw_row in enumerate(rows[HEADER_ROW_COUNT:], start=HEADER_ROW_COUNT + 1):
                self._process_row(raw_row, row_idx, mapping, organization, month, user_columns, cli_user, stats, dry_run)
            if dry_run:
                transaction.set_rollback(True)

        self._print_summary(stats, dry_run, sheet)

    def _load_rows(self, options: dict[str, Any], sheet: str) -> list[list[str]]:
        if options.get("input_json"):
            data = json.loads(Path(options["input_json"]).read_text(encoding="utf-8"))
            return data.get("values", []) or []
        token = os.environ.get("GOOGLE_SHEETS_OAUTH_TOKEN")
        if not token:
            raise CommandError("set GOOGLE_SHEETS_OAUTH_TOKEN env var or use --input-json")
        return _fetch_sheet_values(options["spreadsheet_id"], sheet, token)

    def _process_row(
        self,
        raw_row: list[str],
        row_idx: int,
        mapping: Mapping,
        organization: KippoOrganization,
        month: datetime.date,
        user_columns: dict[int, KippoUser],
        cli_user: KippoUser,
        stats: LoadStats,
        dry_run: bool,
    ) -> None:
        # Pad the row so customer/project indices don't blow up on short rows
        row = list(raw_row) + [""] * (max(USER_COLUMN_START_INDEX, max(user_columns) + 1) - len(raw_row))
        raw_customer = (row[CUSTOMER_COL_INDEX] or "").strip()
        raw_project = (row[PROJECT_COL_INDEX] or "").strip()
        if not raw_customer and not raw_project:
            return  # genuinely blank row

        is_confirmed = (row[CONFIRMED_COL_INDEX] or "").strip() == CONFIRMED_MARKER

        # Resolve customer first (logging only — project lookup is independent of customer FK)
        if _resolve_customer(raw_customer, mapping, organization) is None and raw_customer not in mapping.customer_aliases:
            stats.unresolved_customer.append(f"row {row_idx}: '{raw_customer}'")
            return

        project = _resolve_project(raw_customer, raw_project, mapping, organization)
        if project is None:
            stats.unresolved_project.append(f"row {row_idx}: ({raw_customer}, '{raw_project}')")
            return

        stats.matched += 1
        for col_idx, user in user_columns.items():
            percentage = _parse_percentage(row[col_idx] if col_idx < len(row) else "")
            if percentage is None or percentage == 0:
                stats.skipped_cells += 1
                continue
            self._upsert_assignment(project, user, month, percentage, is_confirmed, cli_user, stats, dry_run)

    @staticmethod
    def _upsert_assignment(
        project: KippoProject,
        user: KippoUser,
        month: datetime.date,
        percentage: int,
        is_confirmed: bool,
        cli_user: KippoUser,
        stats: LoadStats,
        dry_run: bool,
    ) -> None:
        existing = ProjectMonthlyAssignment.objects.filter(project=project, user=user, month=month).first()
        if existing is not None:
            if existing.percentage == percentage and existing.is_confirmed == is_confirmed:
                stats.unchanged += 1
                return
            existing.percentage = percentage
            existing.is_confirmed = is_confirmed
            existing.updated_by = cli_user
            if not dry_run:
                existing.save()
            stats.updated += 1
            return
        if not dry_run:
            ProjectMonthlyAssignment.objects.create(
                project=project,
                user=user,
                month=month,
                percentage=percentage,
                is_confirmed=is_confirmed,
                created_by=cli_user,
                updated_by=cli_user,
            )
        stats.created += 1

    def _print_summary(self, stats: LoadStats, dry_run: bool, sheet: str) -> None:
        prefix = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}sheet {sheet} loaded:"))
        self.stdout.write(f"  matched rows:       {stats.matched}")
        self.stdout.write(f"  assignments created: {stats.created}")
        self.stdout.write(f"  assignments updated: {stats.updated}")
        self.stdout.write(f"  assignments unchanged: {stats.unchanged}")
        self.stdout.write(f"  cells skipped (blank/zero): {stats.skipped_cells}")
        self.stdout.write(f"  unresolved customers: {len(stats.unresolved_customer)}")
        self.stdout.write(f"  unresolved projects:  {len(stats.unresolved_project)}")
        self.stdout.write(f"  unresolved users:     {len(stats.unresolved_user)}")
        for label, items in (
            ("UNRESOLVED CUSTOMERS", stats.unresolved_customer),
            ("UNRESOLVED PROJECTS", stats.unresolved_project),
            ("UNRESOLVED USERS", stats.unresolved_user),
        ):
            sample_limit = 20
            for line in items[:sample_limit]:
                self.stdout.write(self.style.WARNING(f"  {label}: {line}"))
            if len(items) > sample_limit:
                self.stdout.write(self.style.WARNING(f"  {label}: … and {len(items) - sample_limit} more"))
