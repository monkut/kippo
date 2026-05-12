"""Management command to download and install the latest kippo-ui build."""

import os
import shutil
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

KIPPO_UI_REPO = "monkut/kippo-ui"
GITHUB_API_URL = f"https://api.github.com/repos/{KIPPO_UI_REPO}/releases/latest"
DEFAULT_TARBALL_NAME = "kippo-ui-build-prod.tar.gz"
TARBALL_BY_PREFIX = {
    "": "kippo-ui-build.tar.gz",
    "/prod": "kippo-ui-build-prod.tar.gz",
    "/stg": "kippo-ui-build-stg.tar.gz",
    "/dev": "kippo-ui-build-dev.tar.gz",
}
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
HTTP_STATUS_SERVER_ERROR = 500
HTTP_STATUS_TOO_MANY_REQUESTS = 429


class Command(BaseCommand):
    """Download and install the latest kippo-ui build from GitHub releases."""

    help = "Download and install the latest kippo-ui build from GitHub releases"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments."""
        parser.add_argument(
            "--output-dir",
            type=str,
            default=None,
            help="Output directory for UI files (default: static/ui)",
        )
        parser.add_argument(
            "--tarball-name",
            type=str,
            default=None,
            help=(
                f"Name of the release asset tarball to download. "
                f"Overrides --base-prefix when set. "
                f"(default: derived from --base-prefix, falling back to {DEFAULT_TARBALL_NAME})"
            ),
        )
        parser.add_argument(
            "--base-prefix",
            type=str,
            default=os.environ.get("KIPPO_UI_BASE_PREFIX", ""),
            help=(
                "URL prefix the kippo-ui bundle was built for "
                "(e.g. '/prod', '/stg', '/dev'). Used to pick the matching release "
                "tarball. Ignored if --tarball-name is given. "
                f"Defaults to KIPPO_UI_BASE_PREFIX env var or '' (uses {DEFAULT_TARBALL_NAME})."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401
        """Execute the command."""
        output_dir = options["output_dir"]
        dry_run = options["dry_run"]
        tarball_name = self._resolve_tarball_name(options["tarball_name"], options["base_prefix"])

        # Determine output path (source directory, not STATIC_ROOT)
        if output_dir:
            ui_path = Path(output_dir)
        else:
            # Default to static/ui source directory (following Django's recommended workflow)
            # Files are placed here, then collectstatic copies them to STATIC_ROOT
            base_dir = Path(settings.BASE_DIR).parent
            ui_path = base_dir / "static" / "ui"

        if dry_run:
            self.stdout.write(f"Would download tarball: {tarball_name}")
            self.stdout.write(f"Would install UI to: {ui_path}")
            self.stdout.write("Dry run - no changes made")
            return

        # Fetch latest release info
        release = self._fetch_latest_release()
        if not release:
            return

        self.stdout.write(f"Latest release: {release['tag_name']}")

        # Find the tarball asset
        tarball_url = self._find_tarball_url(release, tarball_name)
        if not tarball_url:
            return

        # Clean up existing UI directories
        if ui_path.exists():
            self.stdout.write(f"Removing existing UI source directory: {ui_path}")
            shutil.rmtree(ui_path)

        # Clean STATIC_ROOT/ui directory to ensure collectstatic replaces old files
        if settings.STATIC_ROOT:
            static_root_ui = Path(settings.STATIC_ROOT) / "ui"
            if static_root_ui.exists():
                self.stdout.write(f"Removing existing UI static directory: {static_root_ui}")
                shutil.rmtree(static_root_ui)

        # Download and extract
        if not self._download_and_extract(tarball_url, ui_path, tarball_name):
            return

        self.stdout.write(self.style.SUCCESS(f"UI installed successfully to: {ui_path}"))
        self._show_configuration_help(ui_path)

    @staticmethod
    def _resolve_tarball_name(tarball_name: str | None, base_prefix: str) -> str:
        """Resolve which release asset tarball to download.

        Resolution order (highest precedence first):
            1. Explicit --tarball-name
            2. --base-prefix (or KIPPO_UI_BASE_PREFIX env var) mapped via TARBALL_BY_PREFIX
            3. DEFAULT_TARBALL_NAME
        """
        if tarball_name:
            return tarball_name
        normalized_prefix = (base_prefix or "").rstrip("/")
        if not normalized_prefix:
            return DEFAULT_TARBALL_NAME
        try:
            return TARBALL_BY_PREFIX[normalized_prefix]
        except KeyError:
            known = sorted(p for p in TARBALL_BY_PREFIX if p)
            msg = f"No tarball mapping for --base-prefix={normalized_prefix!r}. Known prefixes: {known}. Use --tarball-name to override explicitly."
            raise CommandError(msg) from None

    def _fetch_latest_release(self) -> dict | None:
        """Fetch the latest release info from GitHub API with retry logic."""
        import json

        self.stdout.write(f"Fetching latest release from {KIPPO_UI_REPO}...")

        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "kippo-update-ui",
        }

        # Use GITHUB_TOKEN if available for higher rate limits
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
            self.stdout.write("Using GITHUB_TOKEN for authenticated request")

        for attempt in range(MAX_RETRIES):
            try:
                request = Request(GITHUB_API_URL, headers=headers)  # noqa: S310
                with urlopen(request, timeout=30) as response:  # noqa: S310
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as e:
                # Retry on 5xx errors or 429 (rate limit)
                if e.code >= HTTP_STATUS_SERVER_ERROR or e.code == HTTP_STATUS_TOO_MANY_REQUESTS:
                    retries_left = MAX_RETRIES - attempt - 1
                    if retries_left > 0:
                        delay = RETRY_DELAY_SECONDS * (attempt + 1)
                        self.stdout.write(self.style.WARNING(f"Request failed with {e.code}, retrying in {delay}s... ({retries_left} retries left)"))
                        time.sleep(delay)
                        continue
                self.stdout.write(self.style.ERROR(f"Failed to fetch release info: {e}"))
                return None
            except Exception as e:  # noqa: BLE001
                self.stdout.write(self.style.ERROR(f"Failed to fetch release info: {e}"))
                return None

        return None

    def _find_tarball_url(self, release: dict, tarball_name: str = DEFAULT_TARBALL_NAME) -> str | None:
        """Find the tarball download URL from release assets."""
        for asset in release.get("assets", []):
            if asset.get("name") == tarball_name:
                return asset.get("browser_download_url")

        self.stdout.write(
            self.style.ERROR(
                f"{tarball_name} not found in release {release['tag_name']}. Available assets: {[a['name'] for a in release.get('assets', [])]}"
            )
        )
        return None

    def _download_and_extract(self, tarball_url: str, ui_path: Path, tarball_name: str = DEFAULT_TARBALL_NAME) -> bool:
        """Download the tarball and extract to the target directory."""
        self.stdout.write(f"Downloading {tarball_name}...")

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                tarball_path = temp_path / tarball_name

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
