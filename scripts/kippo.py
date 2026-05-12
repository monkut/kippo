#!/usr/bin/env python3
"""kippo-cli — operator CLI for interacting with a running kippo instance via its REST API.

This file is the source for the ``kippo-cli`` console script. Dependencies are
declared in scripts/pyproject.toml; install with:

    uvx --from git+https://github.com/monkut/kippo.git#subdirectory=scripts kippo-cli --help

Subcommands:
  project-details   Dump every ActiveKippoProject to a JSON or TOON file.

Authentication: JWT via /api/token/. Credentials and base URL can be supplied as
CLI options or environment variables (KIPPO_USERNAME / KIPPO_PASSWORD / KIPPO_BASE_URL).
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import json
import os
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Any

import requests
from toon import encode as toon_encode

# Distinct from the kippo Django package — this file is run as a script via `uv run`,
# never imported. The kippo package lives under <repo>/kippo/ and is not on sys.path
# when this script is invoked.
PROG = "kippo-cli"

JST = datetime.timezone(datetime.timedelta(hours=9))

TOKEN_PATH = "/api/token/"
PROJECTS_PATH = "/api/projects/"
DEFAULT_PAGE_SIZE = 200  # matches CustomPageNumberPagination.max_page_size


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    project_details = subparsers.add_parser(
        "project-details",
        help="Dump every ActiveKippoProject to a JSON or TOON file.",
        description="Fetches /api/projects/?is_active=true (paginated) and writes the result to a file.",
    )
    _add_auth_arguments(project_details)
    project_details.add_argument(
        "--format",
        choices=("json", "toon"),
        default="json",
        help="Output format (default: json). 'toon' uses python-toon for token-efficient encoding.",
    )
    project_details.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file path. Defaults to active_projects_<YYYYMMDD_HHMMSS>JST.<ext> in the cwd.",
    )
    project_details.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Page size requested from the API (default: {DEFAULT_PAGE_SIZE}).",
    )
    project_details.set_defaults(handler=handle_project_details)

    return parser


def _add_auth_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--base-url",
        default=os.environ.get("KIPPO_BASE_URL"),
        help="Kippo base URL, e.g. https://kippo.example.com (env: KIPPO_BASE_URL)",
    )
    sub.add_argument(
        "--username",
        default=os.environ.get("KIPPO_USERNAME"),
        help="Kippo username (env: KIPPO_USERNAME)",
    )
    sub.add_argument(
        "--password",
        default=os.environ.get("KIPPO_PASSWORD"),
        help="Kippo password (env: KIPPO_PASSWORD; prompts via getpass if absent and stdin is a TTY)",
    )
    sub.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP request timeout in seconds (default: 30).",
    )


def resolve_credentials(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    missing = [name for name, value in (("--base-url", args.base_url), ("--username", args.username)) if not value]
    if missing:
        parser.error(f"missing required argument(s): {', '.join(missing)} (or set the corresponding KIPPO_* env vars)")
    if not args.password:
        # Prefer interactive prompt over --password on argv (visible in process listings)
        if not sys.stdin.isatty():
            parser.error("missing --password (or set KIPPO_PASSWORD); stdin is not a TTY so cannot prompt")
        args.password = getpass.getpass(f"Password for {args.username}: ")
    args.base_url = args.base_url.rstrip("/")


def obtain_access_token(base_url: str, username: str, password: str, timeout: float) -> str:
    response = requests.post(
        f"{base_url}{TOKEN_PATH}",
        json={"username": username, "password": password},
        timeout=timeout,
    )
    if response.status_code != HTTPStatus.OK:
        raise SystemExit(f"authentication failed ({response.status_code}): {response.text.strip()[:500]}")
    return response.json()["access"]


def fetch_active_projects(base_url: str, token: str, page_size: int, timeout: float) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"}
    url: str | None = f"{base_url}{PROJECTS_PATH}?is_active=true&page_size={page_size}"
    results: list[dict[str, Any]] = []
    while url:
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code != HTTPStatus.OK:
            raise SystemExit(f"fetch failed ({response.status_code}): {response.text.strip()[:500]}")
        payload = response.json()
        results.extend(payload.get("results", []))
        url = payload.get("next")
    return results


def default_output_path(fmt: str) -> Path:
    stamp = datetime.datetime.now(tz=JST).strftime("%Y%m%d_%H%M%S")
    return Path.cwd() / f"active_projects_{stamp}JST.{fmt}"


def serialize(projects: list[dict[str, Any]], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(projects, indent=2, ensure_ascii=False, default=str)
    # TOON: key each project by "{name}({id})" instead of the default [N] array marker,
    # so each project is directly addressable. id and name are dropped from the body
    # since they're now in the key.
    keyed = {f"{p['name']}({p['id']})": {k: v for k, v in p.items() if k not in ("id", "name")} for p in projects}
    return toon_encode(keyed)


def handle_project_details(args: argparse.Namespace) -> int:
    token = obtain_access_token(args.base_url, args.username, args.password, args.timeout)
    projects = fetch_active_projects(args.base_url, token, args.page_size, args.timeout)

    output_path = args.output or default_output_path(args.format)
    output_path.write_text(serialize(projects, args.format), encoding="utf-8")
    print(f"wrote {len(projects)} project(s) to {output_path}", file=sys.stderr)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    resolve_credentials(parser, args)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
