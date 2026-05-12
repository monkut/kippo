"""Tests for the update_ui management command, focused on tarball-name resolution."""

import os
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from commons.management.commands.update_ui import Command


class ResolveTarballNameTestCase(SimpleTestCase):
    """Unit tests for the tarball-name resolver helper."""

    def test_no_args_uses_prod_tarball(self):
        """Default resolution returns the prod tarball name (regression guard for #256)."""
        resolved = Command._resolve_tarball_name(tarball_name=None, base_prefix="")
        self.assertEqual(resolved, "kippo-ui-build-prod.tar.gz")

    def test_base_prefix_dev_derives_dev_tarball(self):
        """--base-prefix=/dev maps to kippo-ui-build-dev.tar.gz."""
        resolved = Command._resolve_tarball_name(tarball_name=None, base_prefix="/dev")
        self.assertEqual(resolved, "kippo-ui-build-dev.tar.gz")

    def test_base_prefix_prod_derives_prod_tarball(self):
        """--base-prefix=/prod maps to kippo-ui-build-prod.tar.gz."""
        resolved = Command._resolve_tarball_name(tarball_name=None, base_prefix="/prod")
        self.assertEqual(resolved, "kippo-ui-build-prod.tar.gz")

    def test_base_prefix_stg_derives_stg_tarball(self):
        """--base-prefix=/stg maps to kippo-ui-build-stg.tar.gz."""
        resolved = Command._resolve_tarball_name(tarball_name=None, base_prefix="/stg")
        self.assertEqual(resolved, "kippo-ui-build-stg.tar.gz")

    def test_base_prefix_accepts_trailing_slash(self):
        """--base-prefix=/dev/ resolves identically to --base-prefix=/dev."""
        resolved = Command._resolve_tarball_name(tarball_name=None, base_prefix="/dev/")
        self.assertEqual(resolved, "kippo-ui-build-dev.tar.gz")

    def test_base_prefix_unknown_raises_with_known_list(self):
        """An unmapped --base-prefix raises CommandError listing the known prefixes."""
        with self.assertRaises(CommandError) as ctx:
            Command._resolve_tarball_name(tarball_name=None, base_prefix="/staging")
        message = str(ctx.exception)
        self.assertIn("/staging", message)
        # Known non-empty prefixes should be surfaced to the operator
        self.assertIn("/dev", message)
        self.assertIn("/stg", message)
        self.assertIn("/prod", message)

    def test_explicit_tarball_overrides_base_prefix(self):
        """--tarball-name takes precedence over --base-prefix."""
        resolved = Command._resolve_tarball_name(tarball_name="custom.tar.gz", base_prefix="/dev")
        self.assertEqual(resolved, "custom.tar.gz")


class UpdateUiDryRunTestCase(SimpleTestCase):
    """Integration tests for the dry-run resolution path (no network)."""

    def test_dry_run_default_reports_prod_tarball(self):
        """Dry run with no args prints the prod tarball name and makes no network calls."""
        stdout = StringIO()
        call_command("update_ui", "--dry-run", stdout=stdout)
        output = stdout.getvalue()
        self.assertIn("kippo-ui-build-prod.tar.gz", output)
        self.assertIn("Dry run - no changes made", output)

    def test_dry_run_base_prefix_dev_reports_dev_tarball(self):
        """Dry run with --base-prefix=/dev prints the dev tarball name."""
        stdout = StringIO()
        call_command("update_ui", "--dry-run", "--base-prefix=/dev", stdout=stdout)
        self.assertIn("kippo-ui-build-dev.tar.gz", stdout.getvalue())

    def test_dry_run_explicit_tarball_overrides_base_prefix(self):
        """--tarball-name overrides --base-prefix in the resolved tarball name."""
        stdout = StringIO()
        call_command(
            "update_ui",
            "--dry-run",
            "--tarball-name=explicit.tar.gz",
            "--base-prefix=/dev",
            stdout=stdout,
        )
        output = stdout.getvalue()
        self.assertIn("explicit.tar.gz", output)
        self.assertNotIn("kippo-ui-build-dev.tar.gz", output)

    def test_env_var_kippo_ui_base_prefix_resolves_dev_tarball(self):
        """KIPPO_UI_BASE_PREFIX=/dev with no flag picks the dev tarball."""
        stdout = StringIO()
        with mock.patch.dict(os.environ, {"KIPPO_UI_BASE_PREFIX": "/dev"}):
            call_command("update_ui", "--dry-run", stdout=stdout)
        self.assertIn("kippo-ui-build-dev.tar.gz", stdout.getvalue())

    def test_unknown_base_prefix_raises_command_error(self):
        """Unknown --base-prefix surfaces a CommandError instead of silent fallback."""
        with self.assertRaises(CommandError) as ctx:
            call_command("update_ui", "--dry-run", "--base-prefix=/staging")
        self.assertIn("/staging", str(ctx.exception))
