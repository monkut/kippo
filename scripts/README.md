# scripts/

Standalone CLI utilities for operating on a running kippo instance. Each script
declares its own dependencies inline ([PEP 723](https://peps.python.org/pep-0723/)),
so nothing in this folder needs to live in the project's `pyproject.toml`.

## Running

### Directly from GitHub (no clone required)

`uv run` accepts a URL to a PEP 723 script: it fetches the file, resolves the
inline dependencies into an ephemeral environment, and runs it — without
installing anything globally and without modifying the kippo project's
dependency set.

```bash
uv run https://raw.githubusercontent.com/monkut/kippo/main/scripts/kippo.py --help
```

Pin to a tag or commit by swapping `main` in the URL.

> `uvx` is reserved for tools with a packaged console-script entry point;
> single-file PEP 723 scripts are run with `uv run` instead.

### From a local clone

```bash
uv run scripts/kippo.py --help
```

## kippo.py (`kippo-cli`)

`kippo-cli` is a small multi-command CLI for operators. The file is named
`kippo.py` for brevity, but it is a standalone script — it is never imported,
and it does **not** depend on the `kippo` Django package living under `<repo>/kippo/`.

Run with `--help` to list subcommands:

```bash
uv run scripts/kippo.py --help
```

### Authentication

All subcommands accept the same credentials/base URL options, which fall back
to environment variables. If `--password` and `KIPPO_PASSWORD` are both unset
and stdin is a TTY, the script prompts interactively via `getpass` — preferred,
because passwords passed via `--password` show up in process listings.

| Option       | Env var          |
|--------------|------------------|
| `--base-url` | `KIPPO_BASE_URL` |
| `--username` | `KIPPO_USERNAME` |
| `--password` | `KIPPO_PASSWORD` |

### `project-details`

Fetches every `ActiveKippoProject` (`/api/projects/?is_active=true`) via the
REST API, paginates through every result, and writes the collected list to a
file as JSON (default) or TOON.

```bash
# JSON, default filename (active_projects_<YYYYMMDD_HHMMSS>JST.json in cwd)
uv run scripts/kippo.py project-details \
    --base-url https://kippo.example.com \
    --username alice  # prompts for password

# TOON output, explicit destination, env-var credentials
KIPPO_BASE_URL=https://kippo.example.com \
KIPPO_USERNAME=alice \
KIPPO_PASSWORD=secret \
    uv run scripts/kippo.py project-details --format toon --output /tmp/projects.toon

# Same thing without cloning the repo
uv run https://raw.githubusercontent.com/monkut/kippo/main/scripts/kippo.py \
    project-details --base-url https://kippo.example.com --username alice --format toon
```

TOON encoding uses [python-toon](https://github.com/xaviviro/python-toon) for
a more token-efficient representation when feeding the data to LLMs.
