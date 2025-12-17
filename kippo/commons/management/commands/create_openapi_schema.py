"""Management command to generate OpenAPI schema."""

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Generate OpenAPI schema in YAML or JSON format."""

    help = "Generate OpenAPI schema file in YAML or JSON format"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments."""
        parser.add_argument(
            "--format",
            type=str,
            choices=["yaml", "json"],
            default="yaml",
            help="Output format: yaml or json (default: yaml)",
        )
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Output file path (default: openapi.yaml or openapi.json in project root)",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401
        """Execute the command."""
        output_format = options["format"]
        output_path = options["output"]

        # Default output path based on format
        if output_path is None:
            base_dir = Path(settings.BASE_DIR).parent
            extension = "yaml" if output_format == "yaml" else "json"
            output_path = str(base_dir / f"openapi.{extension}")

        # Map format to spectacular's format parameter
        spectacular_format = "openapi" if output_format == "yaml" else "openapi-json"

        self.stdout.write(f"Generating OpenAPI schema in {output_format.upper()} format...")

        try:
            call_command(
                "spectacular",
                "--file",
                output_path,
                "--format",
                spectacular_format,
                stdout=self.stdout,
                stderr=self.stderr,
            )
            self.stdout.write(self.style.SUCCESS(f"Schema generated: {output_path}"))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"Failed to generate schema: {e}"))
