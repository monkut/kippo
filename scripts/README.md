# kippo-cli

Operator CLI for interacting with a running kippo instance via its REST API.

This directory is a **standalone Python project** — it has its own
`pyproject.toml` and is **not** packaged with the kippo Django project. Its
dependencies (`requests`, `python-toon`) are not part of kippo's runtime deps.

## Install / Run

### Run without installing (`uvx`)

`uvx` builds the package in an ephemeral environment, runs the `kippo-cli`
entry point, and discards the environment when done — nothing pollutes your
system or the kippo venv.

```bash
# Latest commit on main
uvx --from git+https://github.com/monkut/kippo.git#subdirectory=scripts kippo-cli --help

# Pin to a tag or commit
uvx --from "git+https://github.com/monkut/kippo.git@<sha-or-tag>#subdirectory=scripts" kippo-cli --help
```

### Install as a persistent tool

```bash
# uv (creates a managed tool environment; `kippo-cli` lands on PATH)
uv tool install --from git+https://github.com/monkut/kippo.git#subdirectory=scripts

# pipx
pipx install git+https://github.com/monkut/kippo.git#subdirectory=scripts
```

### From a local clone

```bash
cd scripts
uv run kippo-cli --help          # uses scripts/pyproject.toml automatically
```

## Authentication

All subcommands accept the same credentials/base-URL options, which fall back
to environment variables. If `--password` and `KIPPO_PASSWORD` are both unset
and stdin is a TTY, the script prompts via `getpass` — keeps secrets off `ps`.

| Option       | Env var          |
|--------------|------------------|
| `--base-url` | `KIPPO_BASE_URL` |
| `--username` | `KIPPO_USERNAME` |
| `--password` | `KIPPO_PASSWORD` |

## Subcommands

### `project-details`

Paginates `GET /api/projects/?is_active=true` and writes every
`ActiveKippoProject` to a file as JSON (default) or TOON.

```bash
# JSON, default filename (active_projects_<YYYYMMDD_HHMMSS>JST.json in cwd)
kippo-cli project-details \
    --base-url https://kippo.example.com \
    --username alice         # prompts for password

# TOON output, explicit destination, env-var credentials
KIPPO_BASE_URL=https://kippo.example.com \
KIPPO_USERNAME=alice \
KIPPO_PASSWORD=secret \
    kippo-cli project-details --format toon --output /tmp/projects.toon

# Same thing without installing
uvx --from git+https://github.com/monkut/kippo.git#subdirectory=scripts \
    kippo-cli project-details \
    --base-url https://kippo.example.com --username alice --format toon
```

TOON encoding uses [python-toon](https://github.com/xaviviro/python-toon) for a
more token-efficient representation when feeding the data to LLMs.
