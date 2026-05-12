#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests>=2.31",
#     "python-toon>=0.1.3",
# ]
# ///
"""Fetch ActiveKippoProject details via the kippo REST API and dump them to a file.

Authentication: JWT via /api/token/. Credentials and base URL can be supplied as CLI
options or as environment variables (KIPPO_USERNAME / KIPPO_PASSWORD / KIPPO_BASE_URL).

This script is intentionally self-contained — its dependencies are declared inline
(PEP 723) so it can be run with `uv run scripts/get_project_details.py ...` without
adding anything to kippo's project dependencies.
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

JST = datetime.timezone(datetime.timedelta(hours=9))

TOKEN_PATH = "/api/token/"  # noqa: S105 — URL path, not a credential
PROJECTS_PATH = "/api/projects/"
DEFAULT_PAGE_SIZE = 200  # matches CustomPageNumberPagination.max_page_size


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("KIPPO_BASE_URL"),
        help="Kippo base URL, e.g. https://kippo.example.com (env: KIPPO_BASE_URL)",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("KIPPO_USERNAME"),
        help="Kippo username (env: KIPPO_USERNAME)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("KIPPO_PASSWORD"),
        help="Kippo password (env: KIPPO_PASSWORD)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "toon"),
        default="json",
        help="Output format (default: json). 'toon' uses python-toon for token-efficient encoding.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file path. Defaults to active_projects_<YYYYMMDD_HHMMSS>JST.<ext> in the cwd.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Page size requested from the API (default: {DEFAULT_PAGE_SIZE}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP request timeout in seconds (default: 30).",
    )
    args = parser.parse_args()

    missing = [name for name, value in (("--base-url", args.base_url), ("--username", args.username)) if not value]
    if missing:
        parser.error(f"missing required argument(s): {', '.join(missing)} (or set the corresponding KIPPO_* env vars)")
    if not args.password:
        # Prefer interactive prompt over --password on argv (visible in process listings)
        if not sys.stdin.isatty():
            parser.error("missing --password (or set KIPPO_PASSWORD); stdin is not a TTY so cannot prompt")
        args.password = getpass.getpass(f"Password for {args.username}: ")
    args.base_url = args.base_url.rstrip("/")
    return args


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
    return toon_encode(projects)


def main() -> int:
    args = parse_args()
    token = obtain_access_token(args.base_url, args.username, args.password, args.timeout)
    projects = fetch_active_projects(args.base_url, token, args.page_size, args.timeout)

    output_path = args.output or default_output_path(args.format)
    output_path.write_text(serialize(projects, args.format), encoding="utf-8")
    print(f"wrote {len(projects)} project(s) to {output_path}", file=sys.stderr)  # noqa: T201 — CLI status output
    return 0


if __name__ == "__main__":
    sys.exit(main())
