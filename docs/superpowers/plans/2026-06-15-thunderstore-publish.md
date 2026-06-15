# Thunderstore Publish Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 3 of the CI pipeline — a script + workflow to publish pre-built Thunderstore packages via the direct upload API.

**Architecture:** `scripts/publish_thunderstore.sh` handles all logic (generate toml, chunked upload, abort on failure). `.github/workflows/publish-thunderstore.yml` wraps it with input resolution, artifact download, and summary. Plus a small patch to `build-and-validate-thunderstore.yml` to dispatch the publish trigger.

**Tech Stack:** bash, curl, jq, gh CLI, GitHub Actions

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/publish_thunderstore.sh` | Create | All publish logic: config generation, pre-flight checks, multi-step upload API |
| `.github/workflows/publish-thunderstore.yml` | Create | CI wrapper: triggers, input resolution, artifact download, run script |
| `.github/workflows/build-and-validate-thunderstore.yml` | Modify | Add repository_dispatch step to trigger publish after successful validation |
| `docs/superpowers/plans/2026-06-15-thunderstore-publish.md` | Create | This plan file |

---

### Task 1: Create `scripts/publish_thunderstore.sh` — Skeleton and Argument Parsing

**Files:**
- Create: `scripts/publish_thunderstore.sh`

- [ ] **Step 1: Write the script skeleton with argument parsing**

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${ROOT_DIR}/config/mods.json"

MOD_KEY=""
VERSION=""
PACKAGE_ZIP=""
DRY_RUN="false"

usage() {
  cat <<EOF
Usage: publish_thunderstore.sh --mod-key <key> --package-zip <path> [--version <x.y.z>] [--config <path>] [--dry-run]
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --mod-key)
      MOD_KEY="$2"
      shift 2
      ;;
    --version)
      VERSION="$2"
      shift 2
      ;;
    --package-zip)
      PACKAGE_ZIP="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

need_cmd jq
need_cmd curl

if [[ -z "$MOD_KEY" ]]; then
  echo "--mod-key is required" >&2
  exit 1
fi

if [[ -z "$PACKAGE_ZIP" ]]; then
  echo "--package-zip is required" >&2
  exit 1
fi

if [[ ! -f "$PACKAGE_ZIP" ]]; then
  echo "Package zip not found: $PACKAGE_ZIP" >&2
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config not found: $CONFIG_PATH" >&2
  exit 1
fi

# Validate config before proceeding
bash "${ROOT_DIR}/scripts/validate_mods_config.sh" --config "$CONFIG_PATH"
```

- [ ] **Step 2: Make the script executable and verify argument parsing**

Run: `chmod +x scripts/publish_thunderstore.sh`
Run: `bash scripts/publish_thunderstore.sh --help`
Expected: Usage message printed.

Run: `bash scripts/publish_thunderstore.sh`
Expected: Error "--mod-key is required"

Run: `bash scripts/publish_thunderstore.sh --mod-key realtime-weather --package-zip /nonexistent/file.zip`
Expected: Error "Package zip not found: /nonexistent/file.zip"

- [ ] **Step 3: Commit**

```bash
git add scripts/publish_thunderstore.sh
git commit -m "feat: add publish_thunderstore.sh skeleton with argument parsing"
```

---

### Task 2: Pre-Flight Checks and Config Reading

**Files:**
- Modify: `scripts/publish_thunderstore.sh` — append after the argument parsing block

- [ ] **Step 1: Add config reading and pre-flight validation**

Replace the file from the validate line onwards. Keep the skeleton exactly as in Task 1, then replace:
```bash
# Validate config before proceeding
bash "${ROOT_DIR}/scripts/validate_mods_config.sh" --config "$CONFIG_PATH"
```

With:
```bash
# Validate config before proceeding
bash "${ROOT_DIR}/scripts/validate_mods_config.sh" --config "$CONFIG_PATH"

# --- Look up mod in config ---
mod_json="$(jq -c --arg k "$MOD_KEY" '.mods[] | select(.key == $k and .enabled == true)' "$CONFIG_PATH")"
if [[ -z "$mod_json" ]]; then
  echo "Mod key not found or disabled: $MOD_KEY" >&2
  exit 1
fi

# --- Resolve version if not provided ---
if [[ -z "$VERSION" ]]; then
  VERSION="$(jq -r '.version_number' <(unzip -p "$PACKAGE_ZIP" manifest.json) 2>/dev/null || echo "")"
  if [[ -z "$VERSION" ]]; then
    echo "Could not resolve version from package manifest. Pass --version explicitly." >&2
    exit 1
  fi
fi

# --- Extract config values ---
namespace="$(jq -r '.thunderstore.namespace' <<<"$mod_json")"
token_key="$(echo "$namespace" | tr '[:lower:]-' '[:upper:]_')_THUNDER_TOKEN"
name="$(jq -r '.thunderstore.name' <<<"$mod_json")"
description="$(jq -r '.thunderstore.description[0:256]' <<<"$mod_json")"
owner="$(jq -r '.source.owner' <<<"$mod_json")"
repo="$(jq -r '.source.repo' <<<"$mod_json")"
community="$(jq -r '.thunderstore.community' <<<"$mod_json")"
has_nsfw="$(jq -r '.thunderstore.has_nsfw_content // false' <<<"$mod_json")"
deps_json="$(jq -c '.thunderstore.dependencies' <<<"$mod_json")"

# --- Resolve auth token ---
AUTH_TOKEN="${THUNDERSTORE_AUTH_TOKEN:-}"
AUTH_SCHEME="${THUNDERSTORE_AUTH_SCHEME:-Bearer}"
API_BASE="${THUNDERSTORE_API_BASE:-https://thunderstore.io}"

if [[ -z "$AUTH_TOKEN" ]]; then
  echo "THUNDERSTORE_AUTH_TOKEN is not set." >&2
  echo "In CI, this should be injected from secrets.${token_key}" >&2
  exit 1
fi

echo "::group::Publish Pre-flight"
echo "  mod_key:     $MOD_KEY"
echo "  name:        $name"
echo "  version:     $VERSION"
echo "  namespace:   $namespace"
echo "  community:   $community"
echo "  package_zip: $PACKAGE_ZIP"
echo "  api_base:    $API_BASE"
echo "  dry_run:     $DRY_RUN"
echo "::endgroup::"
```

- [ ] **Step 2: Verify config reading with a real mod**

Run:
```bash
THUNDERSTORE_AUTH_TOKEN=fake_token bash scripts/publish_thunderstore.sh \
  --config config/mods.json \
  --mod-key realtime-weather \
  --version 1.0.0 \
  --package-zip build/packages/realtime-weather/1.0.0/Small_tailqwq-RealTimeWeather-1.0.0.zip
```
Expected: Prints pre-flight info with correct namespace `Small_tailqwq`, community `chillwithyou`, name `RealTimeWeather`. Then fails later (no real upload step yet).

- [ ] **Step 3: Commit**

```bash
git add scripts/publish_thunderstore.sh
git commit -m "feat: add config reading and pre-flight checks to publish script"
```

---

### Task 3: Generate `thunderstore.toml` from mods.json

**Files:**
- Modify: `scripts/publish_thunderstore.sh` — append after pre-flight block

- [ ] **Step 1: Write the generate_thunderstore_toml function**

Add after the `need_cmd` function and before the argument parsing loop:

```bash
generate_thunderstore_toml() {
  local out_dir="$1"
  local toml_path="${out_dir}/thunderstore.toml"

  # Convert dependencies JSON array to TOML array of strings
  local deps_toml=""
  deps_toml="$(jq -r '.[]' <<<"$deps_json" | sed 's/^/    "/' | sed 's/$/",/')"
  # Remove trailing comma from last line
  deps_toml="${deps_toml%,}"

  cat > "$toml_path" <<TOML
[package]
namespace = "${namespace}"
name = "${name}"
versionNumber = "${VERSION}"
description = "${description}"
websiteUrl = "https://github.com/${owner}/${repo}"
containsNsfwContent = ${has_nsfw}

[dependencies]
packages = [
${deps_toml}
]

[build]
icon = "icon.png"
readme = "README.md"
TOML

  echo "$toml_path"
}
```

- [ ] **Step 2: Call generate_thunderstore_toml in the main flow**

After the pre-flight `::endgroup::` line, add:

```bash
# --- Generate thunderstore.toml ---
toml_dir="$(mktemp -d)"
if [[ -z "${GITHUB_OUTPUT:-}" ]]; then
  trap 'rm -rf "$toml_dir"' EXIT
fi

toml_path="$(generate_thunderstore_toml "$toml_dir")"
echo "Generated thunderstore.toml:"
cat "$toml_path"
```

- [ ] **Step 3: Verify TOML generation**

Run:
```bash
THUNDERSTORE_AUTH_TOKEN=fake_token bash scripts/publish_thunderstore.sh \
  --config config/mods.json \
  --mod-key realtime-weather \
  --version 1.0.0 \
  --package-zip build/packages/realtime-weather/1.0.0/Small_tailqwq-RealTimeWeather-1.0.0.zip
```
Expected: Prints generated TOML with correct values. `containsNsfwContent = false`. `packages = ["BepInEx-BepInExPack-5.4.2304"]`. Temp dir cleaned up after exit.

- [ ] **Step 4: Commit**

```bash
git add scripts/publish_thunderstore.sh
git commit -m "feat: generate thunderstore.toml from mods.json"
```

---

### Task 4: Implement Upload API Functions (initiate, finish, abort)

**Files:**
- Modify: `scripts/publish_thunderstore.sh` — add upload helper functions after `generate_thunderstore_toml`

- [ ] **Step 1: Add upload helper functions**

Add after the `generate_thunderstore_toml` function:

```bash
post_json() {
  local endpoint="$1"
  local body="$2"
  local out_file="$3"
  local headers=(-H "Content-Type: application/json")
  if [[ -n "$AUTH_TOKEN" ]]; then
    headers+=(-H "Authorization: ${AUTH_SCHEME} ${AUTH_TOKEN}")
  fi
  curl -sS -o "$out_file" -w "%{http_code}" "${headers[@]}" -X POST "${API_BASE}${endpoint}" -d "$body"
}

initiate_upload() {
  local pkg_name="$1"
  local file_size="$2"
  local out_file="$3"

  local body
  body="$(jq -n --arg name "$pkg_name" --argjson size "$file_size" '{name:$name, size:$size}')"
  post_json "/api/experimental/usermedia/initiate-upload/" "$body" "$out_file"
}

finish_upload() {
  local uuid="$1"
  local parts_json="$2"
  local out_file="$3"

  local body
  body="$(jq -n --argjson parts "$parts_json" '{parts:$parts}')"
  post_json "/api/experimental/usermedia/${uuid}/finish-upload/" "$body" "$out_file"
}

abort_upload() {
  local uuid="$1"
  local out_file="$2"
  post_json "/api/experimental/usermedia/${uuid}/abort-upload/" "{}" "$out_file"
}

submit_package() {
  local uuid="$1"
  local author_name="$2"
  local community_slug="$3"
  local categories_json="$4"
  local has_nsfw="$5"
  local out_file="$6"

  local community_categories_json="null"
  if [[ -n "$community_slug" && "$categories_json" != "[]" ]]; then
    community_categories_json="$(jq -n --arg slug "$community_slug" --argjson cats "$categories_json" '{($slug): $cats}')"
  fi

  local body
  body="$(jq -n \
    --arg author "$author_name" \
    --arg community "$community_slug" \
    --argjson nsflag "$has_nsfw" \
    --argjson categories "$categories_json" \
    --argjson ccats "$community_categories_json" \
    '{author_name:$author, communities:[$community], categories:$categories, community_categories:$ccats, has_nsfw_content:$nsflag}')"
  post_json "/api/experimental/usermedia/${uuid}/submit/" "$body" "$out_file"
}
```

- [ ] **Step 2: Verify functions parse/syntax**

Run: `bash -n scripts/publish_thunderstore.sh`
Expected: No syntax errors or warnings.

- [ ] **Step 3: Commit**

```bash
git add scripts/publish_thunderstore.sh
git commit -m "feat: add Thunderstore upload API helper functions"
```

---

### Task 5: Implement Chunked Upload Loop

**Files:**
- Modify: `scripts/publish_thunderstore.sh` — add upload_chunks function after the upload helpers

- [ ] **Step 1: Add the upload_chunks function**

```bash
upload_chunks() {
  local zip_path="$1"
  local upload_urls_json="$2"
  local uuid="$3"

  local chunks_count
  chunks_count="$(jq 'length' <<<"$upload_urls_json")"
  echo "Uploading ${chunks_count} chunk(s)..."

  local parts_json="["
  local idx=0
  while [[ $idx -lt $chunks_count ]]; do
    local url offset length part_num
    url="$(jq -r ".[$idx].url" <<<"$upload_urls_json")"
    offset="$(jq -r ".[$idx].offset" <<<"$upload_urls_json")"
    length="$(jq -r ".[$idx].length" <<<"$upload_urls_json")"
    part_num="$(jq -r ".[$idx].number" <<<"$upload_urls_json")"

    echo "  Chunk ${part_num}/${chunks_count}: offset=${offset} length=${length}"

    # 3 retries with exponential backoff
    local retry=0
    local etag=""
    local temp_chunk
    temp_chunk="$(mktemp)"
    # Extract the chunk bytes from the zip
    dd if="$zip_path" bs=1 skip="$offset" count="$length" of="$temp_chunk" 2>/dev/null

    while [[ $retry -lt 3 ]]; do
      local http_status
      etag=""
      http_status="$(curl -sS -o /dev/null -w "%{http_code}" -D - -X PUT -T "$temp_chunk" "$url" 2>/dev/null | grep -i '^etag:' | sed 's/^[Ee][Tt][Aa][Gg]:[[:space:]]*//' | tr -d '\r\n')"
      local curl_exit=$?
      if [[ $curl_exit -eq 0 && -n "$etag" ]]; then
        break
      fi
      retry=$((retry + 1))
      if [[ $retry -lt 3 ]]; then
        local wait_sec=$((2 ** retry))
        echo "    Retry ${retry}/3 after ${wait_sec}s..."
        sleep "$wait_sec"
      fi
    done

    rm -f "$temp_chunk"

    if [[ -z "$etag" ]]; then
      echo "Failed to upload chunk ${part_num} after 3 retries — aborting" >&2
      return 1
    fi

    if [[ $idx -gt 0 ]]; then
      parts_json+=","
    fi
    parts_json+="$(jq -n --arg tag "$etag" --argjson num "$part_num" '{tag:$tag, number:$num}')"

    idx=$((idx + 1))
  done
  parts_json+="]"

  echo "$parts_json"
}
```

- [ ] **Step 2: Verify syntax**

Run: `bash -n scripts/publish_thunderstore.sh`
Expected: No syntax errors.

- [ ] **Step 3: Commit**

```bash
git add scripts/publish_thunderstore.sh
git commit -m "feat: add chunked upload function with retry logic"
```

---

### Task 6: Wire Up the Main Upload Flow + Dry-Run Mode

**Files:**
- Modify: `scripts/publish_thunderstore.sh` — add the main orchestration block after TOML generation

- [ ] **Step 1: Add the main upload flow**

Replace the TOML generation call and everything after it (search for `# --- Generate thunderstore.toml ---` through the end of file) with:

```bash
# --- Generate thunderstore.toml ---
toml_dir="$(mktemp -d)"
if [[ -z "${GITHUB_OUTPUT:-}" ]]; then
  trap 'rm -rf "$toml_dir"' EXIT
fi

toml_path="$(generate_thunderstore_toml "$toml_dir")"
echo "Generated thunderstore.toml"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "<details><summary>thunderstore.toml</summary>"
    echo ""
    echo '```toml'
    cat "$toml_path"
    echo '```'
    echo ""
    echo "</details>"
  } >> "$GITHUB_STEP_SUMMARY"
fi

# --- Dry-run: stop here ---
if [[ "$DRY_RUN" == "true" ]]; then
  echo ""
  echo "DRY RUN — stopping before upload."
  echo "Would have published ${name} v${VERSION} to ${namespace} on ${community}."
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "published=false" >> "$GITHUB_OUTPUT"
  fi
  exit 0
fi

# --- Initiate upload ---
file_size="$(stat -c%s "$PACKAGE_ZIP")"
echo ""
echo "Initiating upload for ${name} (${file_size} bytes)..."

tmp_response="$(mktemp)"
initiate_upload "$name" "$file_size" "$tmp_response"
init_status=$?

if [[ $init_status -ne 0 || ! -s "$tmp_response" ]]; then
  echo "Initiate upload failed (curl exit $init_status)" >&2
  cat "$tmp_response" >&2 2>/dev/null || true
  rm -f "$tmp_response"
  exit 1
fi

if ! jq -e '.user_media.uuid' "$tmp_response" >/dev/null 2>&1; then
  echo "Initiate upload returned unexpected response:" >&2
  cat "$tmp_response" >&2
  rm -f "$tmp_response"
  exit 1
fi

uuid="$(jq -r '.user_media.uuid' "$tmp_response")"
upload_urls_json="$(jq -c '.upload_urls' "$tmp_response")"
rm -f "$tmp_response"
echo "Upload UUID: ${uuid}"

# --- Upload chunks ---
parts_json="$(upload_chunks "$PACKAGE_ZIP" "$upload_urls_json" "$uuid")"
if [[ -z "$parts_json" ]]; then
  echo "Chunk upload failed — aborting upload ${uuid}" >&2
  abort_upload "$uuid" "$(mktemp)" || true
  exit 1
fi
echo "All chunks uploaded."

# --- Finish upload ---
echo "Finishing upload..."
tmp_finish="$(mktemp)"
finish_status="$(finish_upload "$uuid" "$parts_json" "$tmp_finish")"

if [[ -z "$finish_status" || "$finish_status" -lt 200 || "$finish_status" -ge 300 ]]; then
  echo "Finish upload failed (HTTP ${finish_status})" >&2
  cat "$tmp_finish" >&2 2>/dev/null || true
  abort_upload "$uuid" "$(mktemp)" || true
  rm -f "$tmp_finish"
  exit 1
fi
rm -f "$tmp_finish"
echo "Upload finished successfully."

# --- Submit (publish) ---
echo "Submitting package..."
categories_json="$(jq -r '.thunderstore.categories // ["Mods"]' <<<"$mod_json")"
tmp_submit="$(mktemp)"
submit_status="$(submit_package "$uuid" "$namespace" "$community" "$categories_json" "$has_nsfw" "$tmp_submit")"

if [[ -z "$submit_status" || "$submit_status" -lt 200 || "$submit_status" -ge 300 ]]; then
  echo "Submit failed (HTTP ${submit_status})" >&2
  cat "$tmp_submit" >&2 2>/dev/null || true
  rm -f "$tmp_submit"
  exit 1
fi
rm -f "$tmp_submit"

package_url="${API_BASE}/package/${namespace}/${name}/${VERSION}/"
echo ""
echo "Published: ${package_url}"

# --- GitHub Actions outputs ---
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "published=true"
    echo "package_url=${package_url}"
    echo "uuid=${uuid}"
    echo "namespace=${namespace}"
    echo "name=${name}"
    echo "version=${VERSION}"
    echo "mod_key=${MOD_KEY}"
  } >> "$GITHUB_OUTPUT"
fi

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "## Publish Summary"
    echo ""
    echo "| Field | Value |"
    echo "|-------|-------|"
    echo "| mod_key | ${MOD_KEY} |"
    echo "| name | ${name} |"
    echo "| version | ${VERSION} |"
    echo "| namespace | ${namespace} |"
    echo "| community | ${community} |"
    echo "| url | [${package_url}](${package_url}) |"
    echo "| uuid | ${uuid} |"
  } >> "$GITHUB_STEP_SUMMARY"
fi
```

- [ ] **Step 2: Verify syntax and dry-run**

Run: `bash -n scripts/publish_thunderstore.sh`
Expected: No syntax errors.

Run:
```bash
THUNDERSTORE_AUTH_TOKEN=fake_token bash scripts/publish_thunderstore.sh \
  --config config/mods.json \
  --mod-key realtime-weather \
  --version 1.0.0 \
  --package-zip build/packages/realtime-weather/1.0.0/Small_tailqwq-RealTimeWeather-1.0.0.zip \
  --dry-run
```
Expected: Prints "DRY RUN — stopping before upload. Would have published RealTimeWeather v1.0.0 to Small_tailqwq on chillwithyou."

- [ ] **Step 3: Commit**

```bash
git add scripts/publish_thunderstore.sh
git commit -m "feat: wire up full upload flow in publish script"
```

---

### Task 7: Create `publish-thunderstore.yml` Workflow

**Files:**
- Create: `.github/workflows/publish-thunderstore.yml`

- [ ] **Step 1: Write the complete workflow file**

```yaml
name: Publish Thunderstore Package

on:
  workflow_dispatch:
    inputs:
      mod_key:
        description: Mod key from config/mods.json
        required: true
        type: string
      version:
        description: Optional version, defaults to reading from package manifest
        required: false
        type: string
      artifact_run_id:
        description: GitHub Actions run ID from build-and-validate workflow
        required: true
        type: string
      dry_run:
        description: Dry run (skip actual upload)
        required: false
        type: boolean
        default: true
  repository_dispatch:
    types: [publish-thunderstore]

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Ensure executable scripts
        run: chmod +x scripts/*.sh

      - name: Resolve inputs
        id: resolve
        env:
          EVENT_NAME: ${{ github.event_name }}
          INPUT_MOD_KEY: ${{ inputs.mod_key }}
          INPUT_VERSION: ${{ inputs.version }}
          INPUT_RUN_ID: ${{ inputs.artifact_run_id }}
          INPUT_DRY_RUN: ${{ inputs.dry_run }}
          PAYLOAD_MOD_KEY: ${{ github.event.client_payload.mod_key }}
          PAYLOAD_VERSION: ${{ github.event.client_payload.version }}
          PAYLOAD_RUN_ID: ${{ github.event.client_payload.run_id }}
        run: |
          set -euo pipefail
          MOD_KEY="${INPUT_MOD_KEY:-}"
          VERSION="${INPUT_VERSION:-}"
          RUN_ID="${INPUT_RUN_ID:-}"
          DRY_RUN="${INPUT_DRY_RUN:-true}"

          echo "::group::Incoming event payload"
          echo "event=${EVENT_NAME}"
          echo "input.mod_key=${INPUT_MOD_KEY:-}"
          echo "input.version=${INPUT_VERSION:-}"
          echo "input.artifact_run_id=${INPUT_RUN_ID:-}"
          echo "input.dry_run=${INPUT_DRY_RUN:-}"
          if [[ -n "${PAYLOAD_MOD_KEY:-}" ]]; then
            echo "payload.mod_key=<provided>"
          else
            echo "payload.mod_key=<empty>"
          fi
          if [[ -n "${PAYLOAD_VERSION:-}" ]]; then
            echo "payload.version=<provided>"
          else
            echo "payload.version=<empty>"
          fi
          if [[ -n "${PAYLOAD_RUN_ID:-}" ]]; then
            echo "payload.run_id=<provided>"
          else
            echo "payload.run_id=<empty>"
          fi
          echo "::endgroup::"

          if [[ "${EVENT_NAME}" == "repository_dispatch" ]]; then
            if [[ -n "${PAYLOAD_MOD_KEY:-}" ]]; then MOD_KEY="${PAYLOAD_MOD_KEY}"; fi
            if [[ -n "${PAYLOAD_VERSION:-}" ]]; then VERSION="${PAYLOAD_VERSION}"; fi
            if [[ -n "${PAYLOAD_RUN_ID:-}" ]]; then RUN_ID="${PAYLOAD_RUN_ID}"; fi
            # repository_dispatch: dry-run is false by default (explicit trigger)
            DRY_RUN="false"
          fi

          if [[ -z "${MOD_KEY}" ]]; then
            echo "mod_key is required" >&2
            exit 1
          fi
          if [[ -z "${RUN_ID}" ]]; then
            echo "artifact_run_id is required" >&2
            exit 1
          fi

          {
            echo "mod_key=${MOD_KEY}"
            echo "version=${VERSION}"
            echo "artifact_run_id=${RUN_ID}"
            echo "dry_run=${DRY_RUN}"
          } >> "$GITHUB_OUTPUT"

      - name: Print resolved inputs
        run: |
          set -euo pipefail
          echo "mod_key=${{ steps.resolve.outputs.mod_key }}"
          echo "version=${{ steps.resolve.outputs.version || '<from-manifest>' }}"
          echo "artifact_run_id=${{ steps.resolve.outputs.artifact_run_id }}"
          echo "dry_run=${{ steps.resolve.outputs.dry_run }}"

      - name: Download package artifact
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          RUN_ID="${{ steps.resolve.outputs.artifact_run_id }}"
          MOD_KEY="${{ steps.resolve.outputs.mod_key }}"

          mkdir -p build/packages

          echo "Downloading artifact from run ${RUN_ID}..."
          gh run download "$RUN_ID" \
            --repo "${{ github.repository }}" \
            --name "package-${MOD_KEY}-*" \
            --dir build/packages/downloaded 2>&1 || {
            echo "Failed to download artifact. Check that:" >&2
            echo "  1. The run ID ${RUN_ID} exists" >&2
            echo "  2. The artifact 'package-${MOD_KEY}-*' was uploaded by that run" >&2
            echo "  3. The artifact has not expired" >&2
            exit 1
          }

          # Find the downloaded zip
          ZIP_PATH="$(find build/packages/downloaded -name '*.zip' -print -quit)"
          if [[ -z "$ZIP_PATH" ]]; then
            echo "No zip found in downloaded artifact" >&2
            exit 1
          fi
          echo "artifact_zip=${ZIP_PATH}" >> "$GITHUB_OUTPUT"

      - name: Look up token secret name
        id: token-key
        run: |
          set -euo pipefail
          MOD_KEY="${{ steps.resolve.outputs.mod_key }}"
          CONFIG="config/mods.json"

          NAMESPACE="$(jq -r --arg k "$MOD_KEY" '.mods[] | select(.key == $k) | .thunderstore.namespace' "$CONFIG")"
          TOKEN_KEY="$(echo "$NAMESPACE" | tr '[:lower:]-' '[:upper:]_')_THUNDER_TOKEN"
          echo "namespace=${NAMESPACE}" >> "$GITHUB_OUTPUT"
          echo "token_key=${TOKEN_KEY}" >> "$GITHUB_OUTPUT"

      - name: Publish package
        id: publish
        env:
          THUNDERSTORE_AUTH_TOKEN: ${{ secrets[steps.token-key.outputs.token_key] }}
          THUNDERSTORE_AUTH_SCHEME: ${{ vars.THUNDERSTORE_AUTH_SCHEME }}
        run: |
          set -euo pipefail
          ARGS=(--config config/mods.json --mod-key "${{ steps.resolve.outputs.mod_key }}")
          if [[ -n "${{ steps.resolve.outputs.version }}" ]]; then
            ARGS+=(--version "${{ steps.resolve.outputs.version }}")
          fi
          ARGS+=(--package-zip "${{ steps.download.outputs.artifact_zip }}")
          if [[ "${{ steps.resolve.outputs.dry_run }}" == "true" ]]; then
            ARGS+=(--dry-run)
          fi

          echo "::group::Publish arguments"
          printf 'ARG %q\n' "${ARGS[@]}"
          echo "::endgroup::"

          ./scripts/publish_thunderstore.sh "${ARGS[@]}"

      - name: Print publish outputs
        run: |
          set -euo pipefail
          echo "published=${{ steps.publish.outputs.published }}"
          echo "package_url=${{ steps.publish.outputs.package_url }}"
          echo "uuid=${{ steps.publish.outputs.uuid }}"
```

- [ ] **Step 2: Verify workflow syntax**

Run: `cat .github/workflows/publish-thunderstore.yml | python3 -c "import yaml,sys; yaml.safe_load(sys.stdin); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/publish-thunderstore.yml
git commit -m "feat: add publish-thunderstore workflow"
```

---

### Task 8: Add repository_dispatch to build-and-validate Workflow

**Files:**
- Modify: `.github/workflows/build-and-validate-thunderstore.yml`

- [ ] **Step 1: Add the dispatch step**

Append after the last step (Upload validation logs) in `build-and-validate-thunderstore.yml`:

```yaml
      - name: Dispatch publish event
        if: steps.validate.outputs.validation_warning != 'true'
        uses: peter-evans/repository-dispatch@v3
        with:
          token: ${{ github.token }}
          event-type: publish-thunderstore
          client-payload: |
            {
              "mod_key": "${{ steps.build.outputs.mod_key }}",
              "version": "${{ steps.build.outputs.version }}",
              "run_id": "${{ github.run_id }}"
            }
```

- [ ] **Step 2: Verify the modified workflow**

Run: `cat .github/workflows/build-and-validate-thunderstore.yml | python3 -c "import yaml,sys; yaml.safe_load(sys.stdin); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/build-and-validate-thunderstore.yml
git commit -m "feat: dispatch publish event after successful build-and-validate"
```

---

### Task 9: End-to-End Local Verification

**Files:**
- Verify: `scripts/publish_thunderstore.sh`
- Verify: `.github/workflows/publish-thunderstore.yml`

- [ ] **Step 1: Full dry-run with a real package (if one exists)**

Run:
```bash
# First, check if a built package exists for realtime-weather
ls -la build/packages/realtime-weather/*/Small_tailqwq-RealTimeWeather-*.zip 2>/dev/null || echo "No pre-built package found — build one first with: bash scripts/build_package.sh --mod-key realtime-weather"
```

If a package exists, run:
```bash
THUNDERSTORE_AUTH_TOKEN=fake_token bash scripts/publish_thunderstore.sh \
  --config config/mods.json \
  --mod-key realtime-weather \
  --package-zip "$(ls build/packages/realtime-weather/*/Small_tailqwq-RealTimeWeather-*.zip | head -1)" \
  --dry-run
```
Expected: "DRY RUN — stopping before upload."

- [ ] **Step 2: Test with missing token**

Run:
```bash
bash scripts/publish_thunderstore.sh \
  --config config/mods.json \
  --mod-key realtime-weather \
  --version 1.0.0 \
  --package-zip /some/fake/path.zip \
  --dry-run 2>&1; echo "exit=$?"
```
Expected: clean error about missing token, then missing package zip. Fixes in order.

- [ ] **Step 3: Run bash syntax check on all scripts**

Run:
```bash
for f in scripts/*.sh; do
  echo -n "$f: "
  bash -n "$f" && echo "OK" || echo "FAIL"
done
```
Expected: All scripts pass syntax check.

- [ ] **Step 4: Verify validate_mods_config accepts our config**

Run:
```bash
bash scripts/validate_mods_config.sh --config config/mods.json
```
Expected: "Config valid" or exit 0.

- [ ] **Step 5: Commit any fixes found during verification**

```bash
git add -A
git commit -m "chore: verification fixes for publish pipeline"
```
```

---

### Task 10: Cleanup and Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md to reflect Phase 3**

In the Architecture section of `CLAUDE.md`, find:

```
## Architecture: Two-Phase Pipeline
```

Replace with:

```
## Architecture: Three-Phase Pipeline
```

And after the Phase 2 diagram block, add:

```
Phase 3: scripts/publish_thunderstore.sh
        │  Downloads pre-built/validated package artifact
        │  Generates thunderstore.toml from mods.json
        │  Uploads via Thunderstore usermedia API (initiate → chunks → finish → submit)
        │
        └── .github/workflows/publish-thunderstore.yml  (triggers: workflow_dispatch, repository_dispatch)
```

In the Repository Structure section, add under `scripts/`:

```
  publish_thunderstore.sh      # Phase 3: upload validated package to Thunderstore
```

And under `.github/workflows/`:

```
  publish-thunderstore.yml     # Triggers: workflow_dispatch, repository_dispatch
```

In the Running Scripts Locally section, add:

```bash
# Publish a pre-built package to Thunderstore (dry-run by default)
THUNDERSTORE_AUTH_TOKEN=<token> bash scripts/publish_thunderstore.sh \
  --mod-key realtime-weather --version 1.0.0 \
  --package-zip build/packages/realtime-weather/1.0.0/Small_tailqwq-RealTimeWeather-1.0.0.zip \
  --dry-run

# Actually publish (omit --dry-run)
THUNDERSTORE_AUTH_TOKEN=<token> bash scripts/publish_thunderstore.sh \
  --mod-key realtime-weather --version 1.0.0 \
  --package-zip build/packages/realtime-weather/1.0.0/Small_tailqwq-RealTimeWeather-1.0.0.zip
```

Under Key Design Decisions, update the "No auto-publish" bullet:

- **No auto-publish (manual gate)**: Phase 3 must be triggered explicitly. When triggered from `workflow_dispatch`, `dry_run` defaults to `true`. Only `repository_dispatch` from a successful Phase 2 run sets `dry_run=false` automatically, creating a controlled publish path.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for Phase 3 publish pipeline"
```
```

---

## Verification Checklist

Before marking complete, verify:

- [ ] `bash -n scripts/publish_thunderstore.sh` passes
- [ ] `THUNDERSTORE_AUTH_TOKEN=fake ./scripts/publish_thunderstore.sh --mod-key realtime-weather --package-zip <real-zip> --dry-run` prints "DRY RUN"
- [ ] `THUNDERSTORE_AUTH_TOKEN=fake ./scripts/publish_thunderstore.sh --mod-key nonexistent --package-zip <zip>` exits with "not found or disabled"
- [ ] `.github/workflows/publish-thunderstore.yml` is valid YAML
- [ ] `.github/workflows/build-and-validate-thunderstore.yml` is valid YAML
- [ ] `scripts/validate_mods_config.sh --config config/mods.json` exits 0
