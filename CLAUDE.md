# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A CI pipeline that converts mod GitHub Release assets into validated Thunderstore packages. It does NOT contain any game or mod code — only scripts, configs, templates, and GitHub Actions workflows for automating the `Release → Thunderstore` conversion.

## Architecture: Two-Phase Pipeline

```
GitHub Release (source repo)
        │
        ▼
Phase 1: scripts/sync_release_assets.sh
        │  Downloads release assets per mods.json asset rules
        │  Writes to a dedicated assets/<mod_key> git branch
        │  Files: assets/<mod_key>/<version>/... + _sync_metadata.json
        ▼
assets/<mod_key> branch
        │
        ▼
Phase 2: scripts/build_package.sh
        │  Reads asset branch, generates manifest.json
        │  Syncs README from source repo, rewrites relative links
        │  Produces <namespace>-<name>-<version>.zip
        │
        ├── scripts/rewrite_readme_links.py   (README link rewriting)
        └── scripts/validate_thunderstore.sh  (calls Thunderstore validate API)
```

## Repository Structure

```
config/mods.json              # THE single source of truth — all mod definitions
scripts/
  sync_release_assets.sh      # Phase 1: download & extract release assets → assets branch
  build_package.sh            # Phase 2: assemble zip from assets branch
  validate_thunderstore.sh    # Phase 2: validate manifest/readme/icon via Thunderstore API
  validate_mods_config.sh     # Validates mods.json schema, called by all other scripts
  rewrite_readme_links.py     # Converts relative links in README to absolute GitHub URLs
templates/<mod_key>/          # Fallback README.md + icon.png per mod
build/                        # .gitignored — local/output artifacts
  packages/<mod_key>/<ver>/   # Output zip files
  validation/                 # API validation response JSONs + raw request/response
.github/workflows/
  sync-release-assets.yml     # Triggers: workflow_dispatch, repository_dispatch, cron (hourly)
  build-and-validate-thunderstore.yml  # Triggers: workflow_dispatch, repository_dispatch
```

## Running Scripts Locally

All scripts are bash with standard `--help` flags. They all auto-validate `mods.json` before proceeding.

```bash
# Validate mods.json
./scripts/validate_mods_config.sh --config config/mods.json

# Sync a single mod (download release assets → assets branch)
bash scripts/sync_release_assets.sh --mod-key igpu-savior --tag v1.2.3
bash scripts/sync_release_assets.sh --mod-key igpu-savior  # uses latest release

# Sync all enabled mods
bash scripts/sync_release_assets.sh --all

# Dry-run sync (no branch push)
DRY_RUN=true bash scripts/sync_release_assets.sh --mod-key aichat

# Build package from assets branch
bash scripts/build_package.sh --mod-key igpu-savior --version 1.2.3
bash scripts/build_package.sh --mod-key igpu-savior  # picks latest on branch

# Validate built package against Thunderstore API
THUNDERSTORE_AUTH_TOKEN=<token> bash scripts/validate_thunderstore.sh \
  --manifest build/packages/.../manifest.json \
  --readme build/packages/.../README.md \
  --icon build/packages/.../icon.png \
  --namespace Small_tailqwq

# Rewrite relative links in a README
python3 scripts/rewrite_readme_links.py \
  --input README.md --output fixed.md \
  --owner Small-tailqwq --repo RealTimeWeatherMod --ref v1.0.0 \
  --readme-path README.md
```

**Dependencies:** All scripts require `jq`, `curl`, `git`. Additionally: `build_package.sh` requires `zip` and `python3`; `scripts/sync_release_assets.sh` requires `unzip`.

## Key Design Decisions

- **Separate branches for assets**: Each `assets/<mod_key>` branch is the stable store of downloaded release content. Scripts refuse to overwrite an existing version directory — prevent accidental mutation.
- **No auto-publish**: The pipeline currently stops at validation. Manual or future workflow steps would handle publishing to Thunderstore.
- **Token naming convention**: Thunderstore API tokens are stored as GitHub Secrets named `{NAMESPACE_UPPER_WITH_UNDERSCORE}_THUNDER_TOKEN` (e.g., `SMALL_TAILQWQ_THUNDER_TOKEN`). The `build_package.sh` script computes this key from `thunderstore.namespace` via `tr '[:lower:]-' '[:upper:]_'`.
- **Readme fallback on sync failure**: If `sync_with_source_readme` is true but the source fetch fails, the build falls back to `templates/<mod_key>/README.md` instead of failing.
- **`website_url` points to source repo**: The manifest's `website_url` field is set to `https://github.com/${owner}/${repo}` (the mod's own source repo), not this pipeline repo.
- **Description truncation**: Manifest descriptions are truncated to 256 Unicode *characters* via `jq '.[0:256]'`, not raw bytes — prevents splitting multi-byte UTF-8 characters. (Previously used `cut -c1-256` which counted bytes.)
- **`[[ "$name" != $matcher ]]` is intentional glob matching**: In `sync_release_assets.sh`, the unquoted `$matcher` in `[[ ]]` is bash's glob pattern matching (e.g., `*.dll` matches `RealTimeWeatherMod.dll`). Do NOT add quotes — that changes it from glob to literal string comparison and breaks asset matching.
- **SemVer only (no pre-release)**: `semver_from_tag` only accepts strict `X.Y.Z` format. Tags like `v1.0.0-beta` or `v1.0.0-rc1` will fail. This is intentional.
- **Cron runs daily at UTC 0**: The sync workflow's `schedule` trigger is `0 0 * * *` (once per day). Previously it was `0 * * * *` (hourly), which wasted GitHub API quota.

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
    "package_files": { "readme": "templates/x/README.md", "icon": "templates/x/icon.png",
                       "readme_source": "README.md", "sync_with_source_readme": true }
  }]
}
```

## Git Branch Layout

This repo uses `assets/<mod_key>` branches (e.g. `assets/igpu-savior`, `assets/aichat`) as content storage, entirely separate from the `main` branch which holds the pipeline source. The sync workflow creates or updates these branches from GitHub Actions with commit messages like `sync(igpu-savior): 1.2.3 from Small-tailqwq/iGPUSaviorMod@v1.2.3`.
