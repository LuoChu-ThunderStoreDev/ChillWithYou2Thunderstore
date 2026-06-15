# Thunderstore Publish Workflow Design

**Date:** 2026-06-15
**Status:** Draft
**Scope:** Phase 3 of the CI pipeline — publish validated Thunderstore packages

## Context

The existing pipeline has two phases:

1. **Phase 1** (`sync-release-assets.yml`): Downloads GitHub Release assets into `assets/<mod_key>` branches
2. **Phase 2** (`build-and-validate-thunderstore.yml`): Builds Thunderstore-compatible zip packages from asset branches, validates them against the Thunderstore API, and uploads the zip as a workflow artifact

**The pipeline currently stops at validation.** This design adds Phase 3: publishing validated packages to Thunderstore.

## Goals

- Publish a pre-built, pre-validated package zip to Thunderstore
- Support both manual trigger (`workflow_dispatch`) and automated trigger (`repository_dispatch` from Phase 2)
- Follow existing patterns: standalone bash script + thin workflow wrapper
- Never publish accidentally — dry-run by default during development

## Non-Goals

- Replacing `build_package.sh` with TCLI's build system
- Automatic publishing after every sync (always requires explicit trigger or build→publish chain)
- Supporting pre-release versions (consistent with existing SemVer-only policy)

## Architecture

```text
build-and-validate-thunderstore.yml (Phase 2, existing)
        │
        │  upload-artifact: package-<mod_key>-<version>.zip
        │  repository_dispatch: publish-thunderstore {mod_key, version, run_id}
        ▼
publish-thunderstore.yml (Phase 3, new)
        │
        │  Step 1: Resolve inputs
        │  Step 2: Download package artifact from Phase 2 run
        │  Step 3: Extract zip, generate thunderstore.toml from mods.json
        │  Step 4: Validate pre-flight (check token, version not already published)
        │  Step 5: Upload via Thunderstore API (initiate → upload chunks → finish → submit)
        │  Step 6: Write step summary
        ▼
    Thunderstore 🔼
```

## Design Decisions

### 1. Upload Method: Direct API

TCLI is designed for build+publish in one step — it reads `thunderstore.toml`, builds a zip, then publishes. Our pipeline already has a pre-built, pre-validated zip from Phase 2, making TCLI's build step redundant.

**Decision:** Use direct Thunderstore upload API calls (curl) in the publish script — consistent with how `validate_thunderstore.sh` calls the validation API. Generate a `thunderstore.toml` from `mods.json` as a sidecar artifact for debugging, documentation, and potential future TCLI migration.

The upload flow: `initiate-upload` → `PUT` chunks (saving ETags) → `finish-upload` → `submit`.

### 2. Independent Workflow

A new `publish-thunderstore.yml` separate from `build-and-validate-thunderstore.yml`. This allows:

- Publishing without rebuilding
- Independent triggering
- Different permission scopes

### 3. Download Pre-Built Artifact

The publish workflow downloads the zip artifact from the Phase 2 run (identified by `run_id` in the `repository_dispatch` payload), using `gh run download`. For manual `workflow_dispatch`, the user provides the `run_id`.

This guarantees the published package is byte-for-byte identical to what was validated.

### 4. Generate thunderstore.toml from mods.json

A `generate_thunderstore_toml()` function in the publish script reads `mods.json` and outputs `thunderstore.toml`. `mods.json` remains the single source of truth.

### 5. Token Conventions (reuse existing)

Same pattern as `validate_thunderstore.sh`: `THUNDERSTORE_AUTH_TOKEN` env var with `THUNDERSTORE_AUTH_SCHEME` (default `Bearer`). The workflow resolves the secret via the existing naming convention: `{NAMESPACE_UPPER}_THUNDER_TOKEN`.

## Files to Create

### `scripts/publish_thunderstore.sh`

New bash script (~250-300 lines). Entry point for all publish logic.

**Arguments:**

| Flag | Required | Description |
|------|----------|-------------|
| `--config` | No | Path to mods.json (default: `config/mods.json`) |
| `--mod-key` | Yes | Mod key in mods.json |
| `--version` | No | Version to publish (default: latest on assets branch) |
| `--package-zip` | Yes | Path to the pre-built package zip |
| `--dry-run` | No | Generate configs and validate, but skip upload |

**Functions:**

1. `need_cmd()` — dependency check (reused pattern)
2. `generate_thunderstore_toml()` — read mods.json → write `thunderstore.toml`
3. `initiate_upload()` — `POST /api/experimental/usermedia/initiate-upload` → returns uuid + upload URLs
4. `upload_chunks()` — for each chunk, `PUT` to presigned URL, collect ETags
5. `finish_upload()` — `POST /api/experimental/usermedia/{uuid}/finish-upload` with ETags
6. `submit_package()` — `POST /api/experimental/usermedia/{uuid}/submit` with metadata

**Output variables (GitHub Actions):**

- `published` — `true`/`false`
- `package_url` — URL on thunderstore.io
- `uuid` — upload UUID (for debugging)

**Error handling:**

- Upload failure → abort upload, exit non-zero
- Network retry: 3 attempts with exponential backoff for each chunk
- Token missing → clear error message referencing the expected secret name

### `.github/workflows/publish-thunderstore.yml`

New workflow file.

**Triggers:**

- `workflow_dispatch`: inputs `mod_key` (required), `version` (optional), `artifact_run_id` (required), `dry_run` (optional boolean, default `true`)
- `repository_dispatch`: type `publish-thunderstore`, payload `{mod_key, version, run_id}`

**Permissions:**

- `contents: read` — checkout repo
- `actions: read` — download artifacts from other workflow runs

**Steps:**

1. Checkout (for scripts and mods.json)
2. Resolve inputs (merge dispatch/manual, same pattern as build workflow)
3. Download package artifact via `gh run download`
4. Run `publish_thunderstore.sh`
5. Write step summary

## `thunderstore.toml` Schema (generated from mods.json)

```toml
[package]
namespace = "Small_tailqwq"       # → mods.json: thunderstore.namespace
name = "RealTimeWeather"           # → mods.json: thunderstore.name
versionNumber = "1.2.3"            # → resolved version
description = "实时天气同步插件。"  # → mods.json: thunderstore.description (truncated 256 chars)
websiteUrl = "https://github.com/Small-tailqwq/RealTimeWeatherMod"  # → source.owner + source.repo
containsNsfwContent = false        # → mods.json: thunderstore.has_nsfw_content (default false)

[dependencies]
packages = [
    "BepInEx-BepInExPack-5.4.2304"  # → mods.json: thunderstore.dependencies
]

[build]
icon = "icon.png"
readme = "README.md"
```

## Upload API Flow (detailed)

```text
1. POST /api/experimental/usermedia/initiate-upload
   Body: {"name": "RealTimeWeather", "size": <zip_bytes>}
   Auth: Bearer <token>
   → Response: {"user_media": {"uuid": "..."}, "upload_urls": [{...}, {...}]}

2. For each upload_url (8MB chunks, uploaded concurrently):
   PUT <presigned_url>
   Body: raw bytes of that chunk
   → Response header: ETag (save for finish step)

3. POST /api/experimental/usermedia/{uuid}/finish-upload
   Body: {"parts": [{"tag": "<etag>", "number": 1}, ...]}
   → Response: 200 OK

4. POST /api/experimental/usermedia/{uuid}/submit
   Body: {
     "author_name": "Small_tailqwq",
     "communities": ["chillwithyou"],
     "categories": [],
     "community_categories": {"chillwithyou": ["Mods"]},
     "has_nsfw_content": false
   }
   → Response: 200 OK → published!
```

## mods.json Extensions (optional)

To support the submit metadata, two optional fields may be added to `thunderstore`:

```json
{
  "thunderstore": {
    "community": "chillwithyou",
    "namespace": "Small_tailqwq",
    "name": "RealTimeWeather",
    "has_nsfw_content": false,
    "categories": ["Mods"]
  }
}
```

- `has_nsfw_content` — defaults to `false` if absent
- `categories` — defaults to `["Mods"]` if absent
- `community` — already exists, used as the community slug in submit

## Verification

### Local testing

```bash
# Dry run (no upload)
bash scripts/publish_thunderstore.sh \
  --config config/mods.json \
  --mod-key realtime-weather \
  --version 1.0.0 \
  --package-zip build/packages/realtime-weather/1.0.0/Small_tailqwq-RealTimeWeather-1.0.0.zip \
  --dry-run

# Expect: thunderstore.toml generated, all pre-flight checks pass, upload skipped
```

### CI testing

```bash
# Trigger via gh CLI (dry run)
gh workflow run publish-thunderstore.yml \
  -f mod_key=realtime-weather \
  -f version=1.0.0 \
  -f artifact_run_id=<run_id>
```

### What to verify

- [ ] `thunderstore.toml` is correctly generated from mods.json
- [ ] Script exits cleanly on missing token with helpful error
- [ ] Script exits cleanly on missing package zip
- [ ] `--dry-run` skips all upload steps
- [ ] Upload API calls return expected responses (test with a real token on a test package, or against thunderstore.dev)
- [ ] Workflow resolves inputs correctly from both `workflow_dispatch` and `repository_dispatch`
- [ ] Artifact download from Phase 2 run works

## Open Dependencies

- **`build-and-validate-thunderstore.yml` needs a `repository_dispatch` step:** Phase 2 should send a `publish-thunderstore` repository_dispatch event after successful build+validation. This is a separate change — modify the existing workflow to add a final step (guarded by validation success) that dispatches `{mod_key, version, run_id: github.run_id}`.
