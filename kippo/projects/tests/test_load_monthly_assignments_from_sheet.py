"""Tests for the `load_monthly_assignments_from_sheet` management command."""

import datetime
import json
from io import StringIO
from pathlib import Path

from accounts.models import OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from projects.management.commands import load_monthly_assignments_from_sheet as loader
from projects.models import KippoCustomer, ProjectMonthlyAssignment


def _write_json(tmp_dir: Path, name: str, payload: dict) -> Path:
    path = tmp_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class LoadMonthlyAssignmentsFromSheetTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self) -> None:
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

        # Add a second user + membership so multi-column tests are realistic
        from accounts.models import KippoUser

        self.user2 = KippoUser.objects.create(username="kanji-user-2", github_login="kanji2", email="b@example.com")
        OrganizationMembership.objects.create(
            user=self.user2,
            organization=self.organization,
            is_developer=True,
            created_by=self.user,
            updated_by=self.user,
        )

        # Customer + extra project (the loader looks projects up by `name` within the org)
        self.customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="アクメ",
            created_by=self.user,
            updated_by=self.user,
        )
        self.project.customer = self.customer
        self.project.start_date = datetime.date(2026, 6, 1)
        self.project.save()

        self.tmp = Path(self._get_tmp_dir())
        self.mapping_path = _write_json(
            self.tmp,
            "mapping.json",
            {
                "user_columns": {"書": "octocat", "海": "kanji-user-2"},
                "customer_aliases": {"PAO": None},
                "project_aliases": {"内部|": self.project.name},
            },
        )

    def _get_tmp_dir(self) -> str:
        import tempfile

        d = tempfile.mkdtemp(prefix="kippo_test_loader_")
        self.addCleanup(self._cleanup_tmp, d)
        return d

    def _cleanup_tmp(self, d: str) -> None:
        import shutil

        shutil.rmtree(d, ignore_errors=True)

    def _sheet_payload(self, data_rows: list[list[str]]) -> dict:
        return {
            "values": [
                ["", "202606", "アサインアサイン表"],  # row 1: title
                ["", "No.", "顧客", "プロジェクト内容", "目安売上換算", "書", "海"],  # row 2: header
                ["", "", "", "", "", "100%", "100%"],  # row 3: availability (ignored)
                *data_rows,
            ]
        }

    def _call(self, *, sheet_data: dict, dry_run: bool = False, sheet: str = "202606") -> str:
        sheet_path = _write_json(self.tmp, "sheet.json", sheet_data)
        out = StringIO()
        call_command(
            "load_monthly_assignments_from_sheet",
            "--sheet",
            sheet,
            "--organization",
            self.organization.name,
            "--mapping",
            str(self.mapping_path),
            "--input-json",
            str(sheet_path),
            *(["--dry-run"] if dry_run else []),
            stdout=out,
        )
        return out.getvalue()

    # ---- unit-ish tests on the helpers ------------------------------------

    def test_extract_project_name_hint(self) -> None:
        self.assertEqual(loader._extract_project_name_hint("desc (Canonical Name)"), "Canonical Name")
        self.assertEqual(loader._extract_project_name_hint("Canonical Name"), None)
        self.assertEqual(loader._extract_project_name_hint("desc (中身)"), "中身")

    def test_parse_percentage(self) -> None:
        self.assertEqual(loader._parse_percentage("50%"), 50)
        self.assertEqual(loader._parse_percentage(" 35 "), 35)
        self.assertEqual(loader._parse_percentage(""), None)
        self.assertEqual(loader._parse_percentage("not a number"), None)
        self.assertEqual(loader._parse_percentage("12.7%"), 13)

    # ---- end-to-end command tests ----------------------------------------

    def test_dry_run_creates_no_rows(self) -> None:
        sheet = self._sheet_payload(
            [
                ["確", "1", "アクメ", self.project.name, "100,000", "50%", "30%"],
            ]
        )
        output = self._call(sheet_data=sheet, dry_run=True)
        self.assertIn("[DRY-RUN]", output)
        self.assertEqual(ProjectMonthlyAssignment.objects.count(), 0)

    def test_creates_assignments_for_each_user_column(self) -> None:
        sheet = self._sheet_payload(
            [
                ["確", "1", "アクメ", self.project.name, "100,000", "50%", "30%"],
            ]
        )
        self._call(sheet_data=sheet)
        rows = ProjectMonthlyAssignment.objects.filter(project=self.project, month=datetime.date(2026, 6, 1))
        self.assertEqual(rows.count(), 2)
        by_user = {row.user_id: row for row in rows}
        self.assertEqual(by_user[self.user.id].percentage, 50)
        self.assertEqual(by_user[self.user2.id].percentage, 30)
        self.assertTrue(by_user[self.user.id].is_confirmed)
        self.assertTrue(by_user[self.user2.id].is_confirmed)

    def test_confirmed_marker(self) -> None:
        sheet = self._sheet_payload(
            [
                ["", "1", "アクメ", self.project.name, "0", "40%", ""],
            ]
        )
        self._call(sheet_data=sheet)
        row = ProjectMonthlyAssignment.objects.get(project=self.project, user=self.user)
        self.assertFalse(row.is_confirmed)
        self.assertEqual(row.percentage, 40)

    def test_blank_and_zero_cells_skipped(self) -> None:
        sheet = self._sheet_payload(
            [
                ["", "1", "アクメ", self.project.name, "", "", "0%"],
            ]
        )
        self._call(sheet_data=sheet)
        self.assertEqual(ProjectMonthlyAssignment.objects.count(), 0)

    def test_idempotent_unchanged(self) -> None:
        sheet = self._sheet_payload(
            [
                ["", "1", "アクメ", self.project.name, "", "25%", ""],
            ]
        )
        self._call(sheet_data=sheet)
        output = self._call(sheet_data=sheet)  # second run
        self.assertEqual(ProjectMonthlyAssignment.objects.filter(user=self.user).count(), 1)
        self.assertIn("unchanged: 1", output)

    def test_update_changes_percentage(self) -> None:
        sheet_a = self._sheet_payload(
            [
                ["", "1", "アクメ", self.project.name, "", "25%", ""],
            ]
        )
        self._call(sheet_data=sheet_a)
        sheet_b = self._sheet_payload(
            [
                ["確", "1", "アクメ", self.project.name, "", "60%", ""],
            ]
        )
        output = self._call(sheet_data=sheet_b)
        row = ProjectMonthlyAssignment.objects.get(user=self.user)
        self.assertEqual(row.percentage, 60)
        self.assertTrue(row.is_confirmed)
        self.assertIn("updated: 1", output)

    def test_parenthesized_project_hint(self) -> None:
        sheet = self._sheet_payload(
            [
                ["", "1", "アクメ", f"description (  {self.project.name}  )", "", "40%", ""],
            ]
        )
        self._call(sheet_data=sheet)
        row = ProjectMonthlyAssignment.objects.get(user=self.user)
        self.assertEqual(row.percentage, 40)
        self.assertEqual(row.project_id, self.project.id)

    def test_project_alias_with_blank_project_column(self) -> None:
        # mapping has "内部|" -> self.project.name; sheet row has 顧客=内部, project=blank
        sheet = self._sheet_payload(
            [
                ["", "1", "内部", "", "", "10%", ""],
            ]
        )
        # Need '内部' to resolve as customer-or-aliased-null; add to customer_aliases
        self.mapping_path.write_text(
            json.dumps(
                {
                    "user_columns": {"書": "octocat", "海": "kanji-user-2"},
                    "customer_aliases": {"内部": None},
                    "project_aliases": {"内部|": self.project.name},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._call(sheet_data=sheet)
        row = ProjectMonthlyAssignment.objects.get(user=self.user)
        self.assertEqual(row.percentage, 10)

    def test_unresolved_customer_logged_and_skipped(self) -> None:
        sheet = self._sheet_payload(
            [
                ["", "1", "存在しない顧客", self.project.name, "", "10%", ""],
            ]
        )
        output = self._call(sheet_data=sheet)
        self.assertEqual(ProjectMonthlyAssignment.objects.count(), 0)
        self.assertIn("unresolved customers: 1", output)
        self.assertIn("存在しない顧客", output)

    def test_unresolved_project_logged_and_skipped(self) -> None:
        sheet = self._sheet_payload(
            [
                ["", "1", "アクメ", "存在しないプロジェクト", "", "10%", ""],
            ]
        )
        output = self._call(sheet_data=sheet)
        self.assertEqual(ProjectMonthlyAssignment.objects.count(), 0)
        self.assertIn("存在しないプロジェクト", output)

    def test_invalid_sheet_name_format(self) -> None:
        with self.assertRaises(CommandError):
            self._call(sheet_data=self._sheet_payload([]), sheet="not-a-month")

    def test_missing_user_columns_in_mapping_raises(self) -> None:
        self.mapping_path.write_text(json.dumps({"user_columns": {}}), encoding="utf-8")
        with self.assertRaises(CommandError):
            self._call(
                sheet_data=self._sheet_payload(
                    [
                        ["", "1", "アクメ", self.project.name, "", "10%", ""],
                    ]
                )
            )
