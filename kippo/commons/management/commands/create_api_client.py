"""Management command to generate API client from OpenAPI schema."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Generate API client from OpenAPI schema."""

    help = "Generate Python or TypeScript API client from OpenAPI schema"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments."""
        parser.add_argument(
            "--type",
            type=str,
            choices=["python", "typescript"],
            default="python",
            help="Client type to generate: python or typescript (default: python)",
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            default=None,
            help="Output directory for generated client (default: python-client or ts-client)",
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

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401
        """Execute the command."""
        client_type = options["type"]
        output_dir = options["output_dir"]
        cleanup = options["cleanup"]
        package_name = options["package_name"]

        # Set default output directory based on client type
        if output_dir is None:
            output_dir = "python-client" if client_type == "python" else "ts-client"

        # Get project root directory (kippo/kippo -> kippo)
        base_dir = Path(settings.BASE_DIR).parent
        output_path = base_dir / output_dir
        schema_file = base_dir / "openapi.yaml"

        # Generate schema
        if not self._generate_schema(schema_file):
            return

        # Clean up old client if requested
        if cleanup and output_path.exists():
            self.stdout.write(f"Cleaning up old client at {output_path}...")
            shutil.rmtree(output_path)
            self.stdout.write(self.style.SUCCESS("Old client removed"))

        # Generate client based on type
        try:
            if client_type == "python":
                self._generate_python_client(schema_file, output_path, base_dir, package_name)
            else:
                self._generate_typescript_client(schema_file, output_path, package_name)
        finally:
            # Clean up schema file
            if schema_file.exists():
                schema_file.unlink()
                self.stdout.write("Cleaned up temporary schema file")

    def _generate_schema(self, schema_file: Path) -> bool:
        """Generate OpenAPI schema file."""
        self.stdout.write("Generating OpenAPI schema...")
        try:
            call_command(
                "spectacular",
                "--file",
                str(schema_file),
                "--format",
                "openapi",
                stdout=self.stdout,
                stderr=self.stderr,
            )
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"Failed to generate schema: {e}"))
            return False
        else:
            self.stdout.write(self.style.SUCCESS(f"Schema generated: {schema_file}"))
            return True

    def _generate_python_client(self, schema_file: Path, output_path: Path, base_dir: Path, package_name: str) -> None:
        """Generate Python client using openapi-python-client."""
        # Create config file
        config_file = base_dir / "openapi-client-config.yaml"
        config_content = f"""post_hooks: []
project_name_override: "{package_name.replace("_", "-")}"
package_name_override: "{package_name}"
"""
        config_file.write_text(config_content)

        self.stdout.write(f"Generating Python client to {output_path}...")

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                result = subprocess.run(  # noqa: S603
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

                if result.stdout:
                    self.stdout.write(result.stdout)

                # Find generated client directory
                generated_dirs = [d for d in temp_path.iterdir() if d.is_dir() and not d.name.startswith(".")]
                if not generated_dirs:
                    self.stdout.write(self.style.ERROR("No client directory found after generation"))
                    return

                generated_client = generated_dirs[0]

                if output_path.exists():
                    shutil.rmtree(output_path)
                shutil.move(str(generated_client), str(output_path))

                self.stdout.write(self.style.SUCCESS(f"Python client generated: {output_path}"))
                self._show_python_usage(output_path, package_name)

        except subprocess.CalledProcessError as e:
            self.stdout.write(self.style.ERROR(f"Client generation failed: {e}"))
            self.stdout.write(self.style.ERROR(f"Error output: {e.stderr}"))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"Unexpected error: {e}"))
        finally:
            if config_file.exists():
                config_file.unlink()

    def _generate_typescript_client(self, schema_file: Path, output_path: Path, package_name: str) -> None:
        """Generate TypeScript client using openapi-typescript-codegen."""
        self.stdout.write(f"Generating TypeScript client to {output_path}...")

        try:
            # Ensure output directory exists
            output_path.mkdir(parents=True, exist_ok=True)

            result = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "npx",
                    "openapi-typescript-codegen",
                    "--input",
                    str(schema_file),
                    "--output",
                    str(output_path),
                    "--name",
                    package_name.replace("_", " ").title().replace(" ", "") + "Client",
                    "--useOptions",
                    "--useUnionTypes",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            if result.stdout:
                self.stdout.write(result.stdout)

            self.stdout.write(self.style.SUCCESS(f"TypeScript client generated: {output_path}"))
            self._show_typescript_usage(output_path)

        except subprocess.CalledProcessError as e:
            self.stdout.write(self.style.ERROR(f"Client generation failed: {e}"))
            self.stdout.write(self.style.ERROR(f"Error output: {e.stderr}"))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"Unexpected error: {e}"))

    def _show_python_usage(self, output_path: Path, package_name: str) -> None:
        """Display Python client usage instructions."""
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("Python client generated successfully!"))
        self.stdout.write("=" * 70)
        self.stdout.write(f"\nLocation: {output_path}")
        self.stdout.write("\nUsage:")
        self.stdout.write("\n1. Install the client:")
        self.stdout.write(f"   cd {output_path}")
        self.stdout.write("   poetry install")
        self.stdout.write("\n2. Use in your code:")
        self.stdout.write(f"   from {package_name} import Client")
        self.stdout.write(f"   from {package_name}.api.projects import projects_list")
        self.stdout.write(f"   from {package_name}.models import KippoProject")
        self.stdout.write("")
        self.stdout.write("   client = Client(base_url='http://localhost:8000', token='your-jwt-token')")
        self.stdout.write("   projects = projects_list.sync(client=client)")
        self.stdout.write("\n" + "=" * 70 + "\n")

    def _show_typescript_usage(self, output_path: Path) -> None:
        """Display TypeScript client usage instructions."""
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("TypeScript client generated successfully!"))
        self.stdout.write("=" * 70)
        self.stdout.write(f"\nLocation: {output_path}")
        self.stdout.write("\nUsage:")
        self.stdout.write("\n1. Copy the generated files to your TypeScript project")
        self.stdout.write("\n2. Install dependencies:")
        self.stdout.write("   npm install axios form-data")
        self.stdout.write("\n3. Use in your code:")
        self.stdout.write("   import { KippoApiClientClient } from './client';")
        self.stdout.write("")
        self.stdout.write("   const client = new KippoApiClientClient({")
        self.stdout.write("     BASE: 'http://localhost:8000',")
        self.stdout.write("     TOKEN: 'your-jwt-token',")
        self.stdout.write("   });")
        self.stdout.write("   const projects = await client.projects.projectsList();")
        self.stdout.write("\n" + "=" * 70 + "\n")
