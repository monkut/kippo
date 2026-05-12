# scripts/

Standalone CLI utilities for operating on a running kippo instance. Each script
declares its own dependencies inline (PEP 723) so nothing in this folder needs
to live in the project's `pyproject.toml`.

Run with `uv` (preferred) — it resolves and caches the per-script dependencies
automatically:

```bash
uv run scripts/<script>.py --help
```

## get_project_details.py

Fetches every `ActiveKippoProject` (`/api/projects/?is_active=true`) via the
REST API and writes the result to a file as JSON or TOON.

Credentials and base URL may be passed as CLI options or environment variables.
If `--password` and `KIPPO_PASSWORD` are both unset and stdin is a TTY, the script
prompts for the password interactively (preferred — keeps it off `ps`).

| Option       | Env var          |
|--------------|------------------|
| `--base-url` | `KIPPO_BASE_URL` |
| `--username` | `KIPPO_USERNAME` |
| `--password` | `KIPPO_PASSWORD` |

Examples:

```bash
# JSON, default filename (active_projects_<YYYYMMDD_HHMMSS>JST.json in cwd)
uv run scripts/get_project_details.py \
    --base-url https://kippo.example.com \
    --username alice --password secret

# TOON output, explicit destination
KIPPO_BASE_URL=https://kippo.example.com \
KIPPO_USERNAME=alice \
KIPPO_PASSWORD=secret \
    uv run scripts/get_project_details.py --format toon --output /tmp/projects.toon
```

TOON encoding uses [python-toon](https://github.com/xaviviro/python-toon) for
a more token-efficient representation when feeding the data to LLMs.
