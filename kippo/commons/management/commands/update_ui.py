"""Management command to download and install the latest kippo-ui build."""

import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

KIPPO_UI_REPO = "monkut/kippo-ui"
GITHUB_API_URL = f"https://api.github.com/repos/{KIPPO_UI_REPO}/releases/latest"
TARBALL_NAME = "kippo-ui-build.tar.gz"


class Command(BaseCommand):
    """Download and install the latest kippo-ui build from GitHub releases."""

    help = "Download and install the latest kippo-ui build from GitHub releases"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments."""
        parser.add_argument(
            "--output-dir",
            type=str,
            default=None,
            help="Output directory for UI files (default: staticfiles/ui)",
        )
        parser.add_argument(
            "--cleanup",
            action="store_true",
            help="Remove existing UI directory before installing",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401
        """Execute the command."""
        output_dir = options["output_dir"]
        cleanup = options["cleanup"]
        dry_run = options["dry_run"]

        # Determine output path (source directory, not STATIC_ROOT)
        if output_dir:
            ui_path = Path(output_dir)
        else:
            # Default to static/ui source directory (following Django's recommended workflow)
            # Files are placed here, then collectstatic copies them to STATIC_ROOT
            base_dir = Path(settings.BASE_DIR).parent
            ui_path = base_dir / "static" / "ui"

        if dry_run:
            self.stdout.write(f"Would install UI to: {ui_path}")
            self.stdout.write("Dry run - no changes made")
            return

        # Fetch latest release info
        release = self._fetch_latest_release()
        if not release:
            return

        self.stdout.write(f"Latest release: {release['tag_name']}")

        # Find the tarball asset
        tarball_url = self._find_tarball_url(release)
        if not tarball_url:
            return

        # Clean up existing UI directory if requested
        if cleanup and ui_path.exists():
            self.stdout.write(f"Removing existing UI directory: {ui_path}")
            shutil.rmtree(ui_path)

        # Download and extract
        if not self._download_and_extract(tarball_url, ui_path):
            return

        self.stdout.write(self.style.SUCCESS(f"UI installed successfully to: {ui_path}"))
        self._show_configuration_help(ui_path)

    def _fetch_latest_release(self) -> dict | None:
        """Fetch the latest release info from GitHub API."""
        self.stdout.write(f"Fetching latest release from {KIPPO_UI_REPO}...")

        try:
            request = Request(  # noqa: S310
                GITHUB_API_URL,
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "kippo-update-ui",
                },
            )
            with urlopen(request, timeout=30) as response:  # noqa: S310
                import json

                return json.loads(response.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"Failed to fetch release info: {e}"))
            return None

    def _find_tarball_url(self, release: dict) -> str | None:
        """Find the tarball download URL from release assets."""
        for asset in release.get("assets", []):
            if asset.get("name") == TARBALL_NAME:
                return asset.get("browser_download_url")

        self.stdout.write(
            self.style.ERROR(
                f"{TARBALL_NAME} not found in release {release['tag_name']}. Available assets: {[a['name'] for a in release.get('assets', [])]}"
            )
        )
        return None

    def _download_and_extract(self, tarball_url: str, ui_path: Path) -> bool:
        """Download the tarball and extract to the target directory."""
        self.stdout.write(f"Downloading {TARBALL_NAME}...")

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                tarball_path = temp_path / TARBALL_NAME

                # Download tarball
                request = Request(tarball_url, headers={"User-Agent": "kippo-update-ui"})  # noqa: S310
                with urlopen(request, timeout=120) as response, tarball_path.open("wb") as f:  # noqa: S310
                    shutil.copyfileobj(response, f)

                self.stdout.write(f"Downloaded to: {tarball_path}")

                # Extract tarball
                extract_path = temp_path / "extracted"
                extract_path.mkdir()

                with tarfile.open(tarball_path, "r:gz") as tar:
                    tar.extractall(extract_path, filter="data")  # noqa: S202

                self.stdout.write(f"Extracted to: {extract_path}")

                # The tarball contains the build output (client/ and server/ directories)
                # For static file serving, we want the client/ directory
                client_path = extract_path / "client"

                if not client_path.exists():
                    # If no client/ directory, use the extracted content directly
                    # (might be a flat structure)
                    client_path = extract_path
                    self.stdout.write("No client/ directory found, using root of extracted files")

                # Ensure target directory exists
                ui_path.parent.mkdir(parents=True, exist_ok=True)

                # Move to final location
                if ui_path.exists():
                    shutil.rmtree(ui_path)

                shutil.copytree(client_path, ui_path)

                return True

        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"Failed to download and extract: {e}"))
            return False

    def _show_configuration_help(self, ui_path: Path) -> None:
        """Display configuration instructions."""
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("kippo-ui installed successfully!"))
        self.stdout.write("=" * 70)
        self.stdout.write(f"\nUI source files installed to: {ui_path}")
        self.stdout.write("\nNext step: Run 'collectstatic' to copy files to STATIC_ROOT:")
        self.stdout.write("  uv run python manage.py collectstatic --noinput")
        self.stdout.write("\nThe UI will be served at /static/ui/ when running Django.")
        self.stdout.write("\n" + "=" * 70 + "\n")
