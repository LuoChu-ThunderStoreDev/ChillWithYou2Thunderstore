# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A CI pipeline that converts mod GitHub Release assets into validated Thunderstore packages. It does NOT contain any game or mod code — only Python CLI, configs, templates, and GitHub Actions workflows for automating the `Release → Thunderstore` conversion.

## Architecture: Three-Phase Pipeline

```text
GitHub Release (source repo)
        │
        ▼
Phase 1: python -m thunderstore_pipeline sync
        │  Downloads release assets per mods.json asset rules
        │  Fetches README from source, rewrites links → readme_rewrite
        │  Optionally fetches CHANGELOG.md (if sync_changelog)
        │  Writes to a dedicated assets/<mod_key> git branch
        │  Files: <version>/... + _sync_metadata.json + readme_origin + readme_rewrite
        ▼
assets/<mod_key> branch
        │
        ▼
Phase 2: python -m thunderstore_pipeline build
        │  Reads readme_rewrite from branch, renames to README.md
        │  Reads CHANGELOG.md from branch if present
        │  Generates manifest.json
        │  Produces <namespace>-<name>-<version>.zip
        │
        └── python -m thunderstore_pipeline validate
               (calls Thunderstore validate API, advisory only)

Phase 3: python -m thunderstore_pipeline publish
        │  Downloads pre-built/validated package artifact
        │  Generates thunderstore.toml from mods.json
        │  Uploads via Thunderstore usermedia API (initiate → chunks → finish → submit)
        │
        └── .github/workflows/publish.yml  (triggers: workflow_dispatch, workflow_call)
```

**Triggers:** Each phase workflow supports `workflow_dispatch` (manual/debug) and `workflow_call` (orchestrator chaining). The `orchestrator.yml` runs daily via `schedule` and chains all three phases.

## Repository Structure

```text
config/mods.json                       # THE single source of truth — all mod definitions
thunderstore_pipeline/                 # Python package (unified CLI)
  __init__.py / __main__.py            # python -m thunderstore_pipeline entry
  cli.py                               # Typer CLI with subcommands
  models.py                            # Pydantic schema for mods.json + API types
  config.py                            # Config loading & validation
  gh.py                                # gh CLI wrapper (releases, branches, dispatch)
  thunderstore_api.py                  # Thunderstore HTTP API client (httpx)
  sync.py                              # Phase 1: download assets → branch
  build.py                             # Phase 2: assemble zip + manifest
  validate.py                          # Phase 2: Thunderstore validate API
  publish.py                           # Phase 3: chunked upload + publish
  ci_output.py                         # GITHUB_OUTPUT/SUMMARY/ENV integration
  readme_rewriter.py                   # Relative link → absolute GitHub URL rewriting (used in sync phase)
templates/<mod_key>/                   # icon.png per mod
build/                                 # .gitignored — local/output artifacts
  packages/<mod_key>/<ver>/            # Output zip files
  validation/                          # API validation response JSONs + raw request/response
.github/workflows/
  sync.yml                             # Phase 1: workflow_dispatch + workflow_call
  build-and-validate.yml               # Phase 2: workflow_dispatch + workflow_call
  publish.yml                          # Phase 3: workflow_dispatch + workflow_call
  backfill.yml                         # Manual-only: sync all historical releases
  orchestrator.yml                     # Full pipeline: schedule + workflow_dispatch
.github/actions/setup/action.yml       # Composite action: checkout + uv cache + install
scripts/                               # Legacy shell scripts (kept for reference)
  rewrite_readme_links.py              # Original Python script (migrated to package)
pyproject.toml                         # uv-managed deps (typer, httpx, pydantic)
uv.lock                                # Locked dependency versions
```

## Running Locally

All commands use the unified Python CLI:

```bash
# Validate mods.json
uv run python -m thunderstore_pipeline config-check

# Sync a single mod (download release assets → assets branch)
uv run python -m thunderstore_pipeline sync --mod-key igpu-savior --tag v1.2.3
uv run python -m thunderstore_pipeline sync --mod-key igpu-savior  # uses latest release

# Sync all enabled mods
uv run python -m thunderstore_pipeline sync --all

# Dry-run sync (no branch push)
uv run python -m thunderstore_pipeline sync --mod-key aichat --dry-run

# Build package from assets branch
uv run python -m thunderstore_pipeline build --mod-key igpu-savior --version 1.2.3
uv run python -m thunderstore_pipeline build --mod-key igpu-savior  # picks latest on branch

# Validate built package against Thunderstore API
THUNDERSTORE_AUTH_TOKEN=<token> uv run python -m thunderstore_pipeline validate \
  --manifest build/packages/.../manifest.json \
  --readme build/packages/.../README.md \
  --icon build/packages/.../icon.png \
  --namespace Small_tailqwq

# Publish a pre-built package to Thunderstore (dry-run by default)
THUNDERSTORE_AUTH_TOKEN=<token> uv run python -m thunderstore_pipeline publish \
  --mod-key realtime-weather --version 1.0.0 \
  --package-zip build/packages/realtime-weather/1.0.0/Small_tailqwq-RealTimeWeather-1.0.0.zip \
  --dry-run

# Actually publish (omit --dry-run)
THUNDERSTORE_AUTH_TOKEN=<token> uv run python -m thunderstore_pipeline publish \
  --mod-key realtime-weather --version 1.0.0 \
  --package-zip build/packages/realtime-weather/1.0.0/Small_tailqwq-RealTimeWeather-1.0.0.zip

# Rewrite relative links in a README (programmatic API)
uv run python -c "
from thunderstore_pipeline.readme_rewriter import rewrite_readme_file
rewrite_readme_file('input.md', 'output.md', 'Owner', 'Repo', 'v1.0', 'README.md')
"
```

**Dependencies:** Python 3.12+ managed by uv. System tools: `git`, `zip`, `unzip`. `gh` CLI for GitHub operations (pre-installed on Actions runners). No longer requires `jq` or `curl` — replaced by `httpx` and Python's `json` module.

## CI Workflow Triggers

```bash
# Debug a single phase manually
gh workflow run sync.yml -f mod_key=realtime-weather -f dry_run=true
gh workflow run build-and-validate.yml -f mod_key=realtime-weather
gh workflow run publish.yml -f mod_key=realtime-weather -f artifact_run_id=<run_id>

# Run full pipeline manually
gh workflow run orchestrator.yml

# Auto: orchestrator runs daily at UTC 0 via schedule cron

# Backfill historical versions (manual only)
gh workflow run backfill.yml -f mod_key=realtime-weather
gh workflow run backfill.yml  # all enabled mods
gh workflow run backfill.yml -f dry_run=true  # dry-run first
```

## Concurrency Model

Each workflow uses concurrency groups keyed on `mod_key` to allow parallel processing of different mods while preventing duplicate runs for the same mod:

| Workflow | Concurrency Group | Cancel | Note |
| -------- | ----------------- | ------ | ---- |
| sync.yml | `sync-<mod_key>` or `sync-all` | false | One per mod at a time |
| build-and-validate.yml | `build-<mod_key>` | false | One per mod at a time |
| publish.yml | `publish-<mod_key>` | false | One per mod at a time |
| backfill.yml | `backfill-<mod_key>` or `backfill-all` | false | Manual-only, no schedule |

Orchestrator limits parallel builds with `max-parallel: 2` and serializes publishes with `max-parallel: 1`.

## Key Design Decisions

- **Python CLI replaces shell scripts**: All business logic is in `thunderstore_pipeline/`. Workflows are thin — they only invoke `uv run python -m thunderstore_pipeline <subcommand>`.
- **`gh` CLI for GitHub operations**: Uses `gh api`, `gh release`, `git` via `subprocess.run` instead of raw `curl` + API token management. `gh` is pre-installed on GitHub Actions runners.
- **`httpx` for Thunderstore API**: The Thunderstore usermedia upload API has no CLI wrapper. Python `httpx` with exponential backoff handles chunked uploads.
- **Pydantic for config validation**: `mods.json` is validated at load time by pydantic models — catches schema errors before any API calls. Replaces `validate_mods_config.sh` entirely.
- **Separate branches for assets**: Each `assets/<mod_key>` branch is the stable store of downloaded release content. Scripts refuse to overwrite an existing version directory — prevent accidental mutation.
- **No auto-publish (manual gate)**: Phase 3 must be triggered explicitly. When triggered from `workflow_dispatch`, `dry_run` defaults to `true`. The orchestrator sets `dry_run=false` for the automated path.
- **Token naming convention**: Thunderstore API tokens are stored as GitHub Secrets named `{NAMESPACE_UPPER_WITH_UNDERSCORE}_THUNDER_TOKEN` (e.g., `SMALL_TAILQWQ_THUNDER_TOKEN`). The `build.py` computes this from `thunderstore.namespace` via `namespace.upper().replace('-', '_')`.
- **README is mandatory**: sync aborts if README fetch fails (Thunderstore requires it). `sync_readme: false` means the build will fail. CHANGELOG is opt-in (`sync_changelog`), best-effort — absent is fine.
- **`website_url` points to source repo**: The manifest's `website_url` field is set to `https://github.com/${owner}/${repo}` (the mod's own source repo), not this pipeline repo.
- **Description truncation**: Manifest descriptions are truncated to 256 Unicode *characters* via Python string slicing `[:256]`, not raw bytes — prevents splitting multi-byte UTF-8 characters.
- **Glob matching uses `fnmatch`**: Asset rule `matcher` fields use Python's `fnmatch.fnmatch` for shell-style glob matching (e.g., `*.dll` matches `RealTimeWeatherMod.dll`).
- **SemVer only (no pre-release)**: `_semver_from_tag` only accepts strict `X.Y.Z` format. Tags like `v1.0.0-beta` or `v1.0.0-rc1` will fail. This is intentional.
- **Cron runs daily at UTC 0**: The orchestrator's `schedule` trigger is `0 0 * * *` (once per day).
- **Validation is advisory**: The `validate` subcommand exits 0 even on API validation failure — it writes warnings to `validation_warning` output. This matches the original shell script behavior.

## mods.json Schema (config-driven pipeline)

```json
{
  "mods": [{
    "key": "unique-id",
    "enabled": true,
    "source":       { "owner": "...", "repo": "..." },
    "assets": [{ "matcher": "*.dll", "kind": "file", "target": "BepInEx/plugins/" },
               { "matcher": "*.zip", "kind": "zip", "extract": [{"from": "...", "to": "..."}],
                 "preserve_unmatched": true, "exclude": ["..."] }],
    "thunderstore": { "community": "...", "namespace": "...", "name": "...",
                      "description": "...", "dependencies": ["..."] },
    "package_files": { "icon": "templates/x/icon.png",
                       "readme_source": "README.md", "sync_readme": true,
                       "sync_changelog": false, "changelog_source": "CHANGELOG.md" }
  }]
}
```

## Git Branch Layout

This repo uses `assets/<mod_key>` branches (e.g. `assets/igpu-savior`, `assets/aichat`) as content storage, entirely separate from the `main` branch which holds the pipeline source. Version directories sit directly at branch root (`<version>/BepInEx/...`, `<version>/readme_rewrite`, etc.). The sync workflow creates or updates these branches from GitHub Actions with commit messages like `sync(igpu-savior): 1.2.3 from Small-tailqwq/iGPUSaviorMod@v1.2.3`.
