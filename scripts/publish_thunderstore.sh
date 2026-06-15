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

generate_thunderstore_toml() {
  local out_dir="$1"
  local toml_path="${out_dir}/thunderstore.toml"

  # Convert dependencies JSON array to TOML array of strings
  local deps_toml=""
  deps_toml="$(jq -r '.[]? // empty' <<<"$deps_json" | sed 's/^/    "/' | sed 's/$/",/')"
  # Remove trailing comma from last line
  deps_toml="${deps_toml%,}"

  # Escape backslashes and double quotes in description for TOML string
  local desc_escaped="${description//\\/\\\\}"
  desc_escaped="${desc_escaped//\"/\\\"}"

  cat > "$toml_path" <<TOML
[package]
namespace = "${namespace}"
name = "${name}"
versionNumber = "${VERSION}"
description = "${desc_escaped}"
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

    # Extract the chunk bytes from the zip
    local temp_chunk
    temp_chunk="$(mktemp)"
    dd if="$zip_path" bs=1 skip="$offset" count="$length" of="$temp_chunk" 2>/dev/null

    # 3 retries with exponential backoff
    local retry=0
    local etag=""
    while [[ $retry -lt 3 ]]; do
      local response_headers
      response_headers="$(mktemp)"
      local http_status
      http_status="$(curl -sS -o /dev/null -w "%{http_code}" -D "$response_headers" -X PUT -T "$temp_chunk" "$url" 2>/dev/null)"
      local curl_exit=$?

      if [[ $curl_exit -eq 0 && "$http_status" -ge 200 && "$http_status" -lt 300 ]]; then
        etag="$(grep -i '^etag:' "$response_headers" | sed 's/^[Ee][Tt][Aa][Gg]:[[:space:]]*//' | tr -d '\r\n')"
        rm -f "$response_headers"
        if [[ -n "$etag" ]]; then
          break
        fi
      fi

      rm -f "$response_headers"
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
need_cmd unzip

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

if [[ $init_status -ne 0 ]]; then
  echo "Initiate upload failed (curl returned non-zero exit)" >&2
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
  tmp_abort="$(mktemp)"
  abort_upload "$uuid" "$tmp_abort" || true
  rm -f "$tmp_abort"
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
  tmp_abort="$(mktemp)"
  abort_upload "$uuid" "$tmp_abort" || true
  rm -f "$tmp_abort" "$tmp_finish"
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
