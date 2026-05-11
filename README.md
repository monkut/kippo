# kippo README

*kippo* is intended to be a light-weight project tracker.

## Installation

1. Install python 3.13.X

2. clone project from github
    https://github.com/monkut/kippo.git

3. Create the virtualenv and install the requirements:

    > Note:
    > This will use the 'pipenv' created and added in the virtual environment

    ```
    $ pipenv install
    ```

## Local Development

Prerequisites:

- [docker](https://store.docker.com/search?type=edition&offering=community)
- [pgcli](https://www.pgcli.com/) (for local db creation)
- [python 3.13](https://www.python.org/downloads/release/python-3133/)
- [pipenv](https://docs.pipenv.org/)

1. Install development requirements:

    ```
    pipenv install --dev

    # enter environment
    pipenv shell
    ```
    
2. Setup `pre-commit` hooks (_black_, _isort_):

    ```bash
    # assumes pre-commit is installed on system via: `pip install pre-commit`
    pre-commit install
    ```
    
    
3. Configure environment variables:

    > `kippo/kippo/settings.py` reads all deployment-specific values from the environment.
    > For local development, only `DEBUG` and the postgres connection vars need to be set;
    > everything else falls back to sensible defaults defined in `settings.py`.

    ```bash
    export DEBUG=True
    export DB_NAME=kippo
    export DB_USER=postgres
    export DB_PASSWORD=mysecretpassword
    export DB_HOST=127.0.0.1
    export DB_PORT=5432
    ```

    Optional variables that affect serving:

    - `URL_PREFIX` — prefix prepended to `STATIC_URL`, `LOGIN_REDIRECT_URL`, etc. Required when the
      app is mounted under a stage path such as `/prod/` (e.g. on AWS API Gateway).
    - `STATIC_URL` is **derived** as `f"{URL_PREFIX}/static/"` — do not set it directly.
    - `ALLOWED_HOSTS` — comma-separated list; defaults to `*`.

5. Setup database:

    ```
    # From the repository root run the following
    docker run --name postgres -e POSTGRES_PASSWORD=mysecretpassword -e POSTGRES_USER=postgres -p 5432:5432 -d postgres
    
    # create the database in the container
    pgcli -h localhost -p 5432 -U postgres -W
    
    # Create the database (make sure it matches the name defined in your kippo.settings.local configuration)
    > CREATE DATABASE kippo;
    > \q
    
    # Make migrations and migrate (create tables in the database)
    cd kippo
    python manage.py makemigrations
    python manage.py migrate
    
    # Load initial fixtures
    python manage.py loaddata `default_columnset`
    python manage.py loaddata default_labelset
    
    # Create management users
    python manage.py loaddata required_bot_users
    
    # load countries to database
    # - loads countries from accounts/fixtures/countries.csv
    python manage.py loadcountries
   
    # create bucket
    `python manage.py `create_required_buckets``
    ```
   
### Test Fixtures

The following fixtures are prepared for local testing and development of the admin:

```bash
python manage.py loaddata testdata`
```

creates the following:

Organizations:
- org1
- org2

User:
- admin (org1, org2)
- org1-user1 (org1)
- org2-user1 (org2)
- dualorg-user3 (org1, org2)
- unassigned-org1 (auto-created for org)
- unassigned-org2 (auto-created for org)

Required Bot Users:
- cli-manager
- github-manager

Where user passwords are set to: 5up3r-53cr3t-p@$$w0rd


## Deploying the kippo-ui SPA

The Django backend serves the [monkut/kippo-ui](https://github.com/monkut/kippo-ui)
React SPA under `/ui/`. The SPA bundle is **not** vendored in this repo — it is downloaded
from the latest `monkut/kippo-ui` GitHub release at deploy time by the `update_ui`
management command.

### One-step deploy (recommended)

```bash
uv run poe update-ui
```

This poe task is a sequence that runs:

```bash
uv run python manage.py update_ui
uv run python manage.py collectstatic --noinput
```

`update_ui` downloads `kippo-ui-build-prod.tar.gz` from the latest release of
`monkut/kippo-ui` and extracts the `client/` directory into `static/ui/`
(at the repo root). `collectstatic` then copies it into `STATIC_ROOT`
(`kippo/staticfiles/`) where whitenoise can serve it.

> **`collectstatic` is mandatory after `update_ui`.** Without it, `/ui/*` returns
> 404 from Django even though the API still works — the `update_ui` step alone
> writes to the source directory only, not to `STATIC_ROOT`.

### Stage-matched UI bundles (`--base-prefix` / `KIPPO_UI_BASE_PREFIX`)

The kippo-ui Vite bundle hard-codes its asset `base` URL at build time. To serve
the SPA from a non-prod API Gateway stage (e.g. the `dev` stage where assets
live under `/dev/static/ui/...`), select the stage-matched tarball with
`--base-prefix`:

```bash
# Dev stage (Zappa dev stage, URL_PREFIX=dev)
uv run python manage.py update_ui --base-prefix=/dev
uv run python manage.py collectstatic --noinput

# Or via env var (no poe-task changes needed)
KIPPO_UI_BASE_PREFIX=/dev uv run poe update-ui
```

Resolution order, highest precedence first:

1. `--tarball-name <name>` — explicit override (existing escape hatch from #256)
2. `--base-prefix <prefix>` — mapped via the `TARBALL_BY_PREFIX` table
3. `KIPPO_UI_BASE_PREFIX` env var — same mapping as `--base-prefix`
4. Default — `kippo-ui-build-prod.tar.gz` (preserved, no production regression)

Known prefixes and their tarballs:

| `--base-prefix` | Release asset                  |
| ---             | ---                            |
| (unset / `""`)  | `kippo-ui-build-prod.tar.gz`   |
| `/prod`         | `kippo-ui-build-prod.tar.gz`   |
| `/dev`          | `kippo-ui-build-dev.tar.gz`    |

An unknown `--base-prefix` (e.g. `/staging`) fails fast with a `CommandError`
that lists the known prefixes — use `--tarball-name=<name>` to override
explicitly when needed.

> **Note**: the `/dev` path requires the matching `monkut/kippo-ui` CI change
> that publishes `kippo-ui-build-dev.tar.gz` alongside the existing prod tarball
> to be merged and a release cut. Until then, `--base-prefix=/dev` will fail at
> the asset-lookup step with "tarball not found in release".

### GitHub API rate limits

`update_ui` calls the unauthenticated GitHub API. If you hit a 403 rate-limit,
set a personal access token (any token with `public_repo` scope) and rerun:

```bash
export GITHUB_TOKEN=<your-pat>
uv run poe update-ui
```

The retry path in `update_ui.py` currently handles only 5xx and 429 — 403s are
not retried automatically.

### How the UI is mounted

The Django URL conf (`kippo/kippo/urls.py`) catches every path under `/ui/` with a
`SPAView` that returns the SPA's `index.html` so React Router can take over
client-side. SPA assets (JS/CSS) are served from `/static/ui/assets/` by whitenoise.

If `SPAView` raises `Http404("UI not installed. Run 'uv run poe update-ui' to install.")`,
either `update_ui` was never run or `collectstatic` was skipped after running it.

## Static files configuration

kippo uses [whitenoise](https://whitenoise.evans.io/) middleware to serve static
files in production. Relevant settings (in `kippo/kippo/settings.py`):

| Setting | Value | Notes |
| --- | --- | --- |
| `STATIC_URL` | `f"{URL_PREFIX}/static/"` | Derived from `URL_PREFIX` env var |
| `STATIC_ROOT` | `<repo>/kippo/staticfiles/` | Target of `collectstatic` |
| `STATICFILES_DIRS` | `[("ui", "<repo>/static/ui/")]` if present | Populated by `update_ui` |
| `WHITENOISE_STATIC_PREFIX` | `/static/` | See whitenoise issue #164 |
| `STATICFILES_STORAGE` | `whitenoise.storage.CompressedManifestStaticFilesStorage` | See note below |

> **Note on `STATICFILES_STORAGE` and Django 5.2.** The setting was deprecated in
> Django 4.2 and removed in 5.1, so on the current Django 5.2 dependency it is
> silently ignored — manifest hashing and `.gz` precompression are **not** active.
> Whitenoise still serves `/static/*` from `STATIC_ROOT` via middleware, so files
> load correctly, just without cache-busting hashes. Migration to the Django 5.1+
> `STORAGES` dict is tracked in [#258](https://github.com/monkut/kippo/issues/258).
> Any such migration must exclude the `static/ui/` bundle from manifest re-hashing
> because Vite already pre-hashes those filenames and the SPA's `index.html`
> hard-codes them.

## Optional Features

### ProjectId Mapping file output

Optionally, the environment variable, `PROJECTID_MAPPING_JSON_S3URI` may be defined to periodically write the *Active* 
ProjectIds to Project names in the following json format:

```json
{
    "last_updated": "2020-10-01T01:10:00+9:00",
    "{KippoProject.id (uuid)}":  "{KippoProject.name}"
}
```

> NOTE: appropriate permissions need to be applied to the related kippo execution role

To enable this feature the envar must be defined and related Cloudwatch event set to fire the following handler periodically (daily expected):

`projects.handlers.functions.handle_write_projectid_mapping_event` 