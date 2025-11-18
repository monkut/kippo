"""Management command to generate Python API client from OpenAPI schema."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Generate Python API client from OpenAPI schema using openapi-python-client."""

    help = "Generate Python API client from OpenAPI schema"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments."""
        parser.add_argument(
            "--output-dir",
            type=str,
            default="python-client",
            help="Output directory for generated client (default: python-client)",
        )
        parser.add_argument(
            "--package-name",
            type=str,
            default="kippo_api_client",
            help="Package name for generated client (default: kippo_api_client)",
        )
        parser.add_argument(
            "--cleanup",
            action="store_true",
            help="Remove existing client directory before generating",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401, PLR0915
        """Execute the command."""
        output_dir = options["output_dir"]
        cleanup = options["cleanup"]

        # Get project root directory (kippo/kippo -> kippo)
        base_dir = Path(settings.BASE_DIR).parent
        output_path = base_dir / output_dir
        schema_file = base_dir / "openapi.yaml"

        self.stdout.write("📋 Generating OpenAPI schema...")

        try:
            # Generate OpenAPI schema using drf-spectacular
            call_command(
                "spectacular",
                "--file",
                str(schema_file),
                "--format",
                "openapi",
                stdout=self.stdout,
                stderr=self.stderr,
            )
            self.stdout.write(self.style.SUCCESS(f"✅ Schema generated: {schema_file}"))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"❌ Failed to generate schema: {e}"))
            return

        # Clean up old client if requested
        if cleanup and output_path.exists():
            self.stdout.write(f"🧹 Cleaning up old client at {output_path}...")
            shutil.rmtree(output_path)
            self.stdout.write(self.style.SUCCESS("✅ Old client removed"))

        # Create config file to disable post-generation hooks
        config_file = base_dir / "openapi-client-config.yaml"
        config_content = """post_hooks: []
project_name_override: "kippo-api-client"
package_name_override: "kippo_api_client"
"""
        config_file.write_text(config_content)

        # Generate Python client using openapi-python-client
        self.stdout.write(f"📦 Generating Python client to {output_path}...")

        try:
            # Use temporary directory for initial generation
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # Run openapi-python-client generate with config
                result = subprocess.run(  # noqa: S603, S607
                    [  # noqa: S607
                        "uv",
                        "run",
                        "openapi-python-client",
                        "generate",
                        "--path",
                        str(schema_file),
                        "--output-path",
                        str(temp_path),
                        "--config",
                        str(config_file),
                        "--meta",
                        "poetry",
                        "--overwrite",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Show output
                if result.stdout:
                    self.stdout.write(result.stdout)

                # Find generated client directory (openapi-python-client creates a subdirectory)
                generated_dirs = [d for d in temp_path.iterdir() if d.is_dir() and not d.name.startswith(".")]

                if not generated_dirs:
                    self.stdout.write(self.style.ERROR("❌ No client directory found after generation"))
                    return

                generated_client = generated_dirs[0]

                # Move to final location
                if output_path.exists():
                    shutil.rmtree(output_path)
                shutil.move(str(generated_client), str(output_path))

                self.stdout.write(self.style.SUCCESS(f"✅ Python client generated: {output_path}"))

        except subprocess.CalledProcessError as e:
            self.stdout.write(self.style.ERROR(f"❌ Client generation failed: {e}"))
            self.stdout.write(self.style.ERROR(f"Error output: {e.stderr}"))
            return
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"❌ Unexpected error: {e}"))
            return
        finally:
            # Clean up temporary files
            if schema_file.exists():
                schema_file.unlink()
                self.stdout.write("🧹 Cleaned up temporary schema file")
            if config_file.exists():
                config_file.unlink()
                self.stdout.write("🧹 Cleaned up config file")

        # Display usage instructions
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("✅ Python client generated successfully!"))
        self.stdout.write("=" * 70)
        self.stdout.write(f"\n📍 Location: {output_path}")
        self.stdout.write("\n📖 Usage:")
        self.stdout.write("\n1. Install the client:")
        self.stdout.write(f"   cd {output_path}")
        self.stdout.write("   poetry install")
        self.stdout.write("\n2. Use in your code:")
        self.stdout.write("   from kippo_api_client import Client")
        self.stdout.write("   from kippo_api_client.api.projects import projects_list")
        self.stdout.write("   from kippo_api_client.models import KippoProject")
        self.stdout.write("")
        self.stdout.write("   client = Client(base_url='http://localhost:8000', token='your-jwt-token')")
        self.stdout.write("   projects = projects_list.sync(client=client)")
        self.stdout.write("\n" + "=" * 70 + "\n")
