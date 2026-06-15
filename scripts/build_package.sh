#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${ROOT_DIR}/config/mods.json"

MOD_KEY=""
VERSION=""

usage() {
  cat <<EOF
Usage: build_package.sh --mod-key <key> [--version <x.y.z>] [--config <path>]
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

try_sync_readme() {
  local owner="$1"
  local repo="$2"
  local ref="$3"
  local readme_rel_path="$4"
  local out_path="$5"

  local tmp_raw
  tmp_raw="$(mktemp)"
  local raw_url="https://raw.githubusercontent.com/${owner}/${repo}/${ref}/${readme_rel_path}"

  if ! curl -fsSL "$raw_url" -o "$tmp_raw"; then
    rm -f "$tmp_raw"
    return 1
  fi

  python3 "${ROOT_DIR}/scripts/rewrite_readme_links.py" \
    --input "$tmp_raw" \
    --output "$out_path" \
    --owner "$owner" \
    --repo "$repo" \
    --ref "$ref" \
    --readme-path "$readme_rel_path"

  rm -f "$tmp_raw"
  return 0
}

latest_version_from_branch() {
  local branch="$1"
  local mod_key="$2"
  git ls-tree -r --name-only "origin/${branch}" "assets/${mod_key}" \
    | awk -F/ 'NF>=4 {print $3}' \
    | sort -Vu \
    | tail -n 1
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
need_cmd git
need_cmd zip
need_cmd curl
need_cmd python3

if [[ -z "$MOD_KEY" ]]; then
  echo "--mod-key is required" >&2
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config not found: $CONFIG_PATH" >&2
  exit 1
fi

bash "${ROOT_DIR}/scripts/validate_mods_config.sh" --config "$CONFIG_PATH"

mod_json="$(jq -c --arg k "$MOD_KEY" '.mods[] | select(.key == $k and .enabled == true)' "$CONFIG_PATH")"
if [[ -z "$mod_json" ]]; then
  echo "Mod key not found or disabled: $MOD_KEY" >&2
  exit 1
fi

branch="assets/${MOD_KEY}"
git fetch origin "$branch" >/dev/null 2>&1 || {
  echo "Remote branch not found: $branch" >&2
  exit 1
}

if [[ -z "$VERSION" ]]; then
  VERSION="$(latest_version_from_branch "$branch" "$MOD_KEY")"
fi

if [[ -z "$VERSION" ]]; then
  echo "No version found under branch ${branch}" >&2
  exit 1
fi

asset_prefix="assets/${MOD_KEY}/${VERSION}"
if ! git ls-tree -r --name-only "origin/${branch}" "$asset_prefix" | grep -q .; then
  echo "Version directory missing in ${branch}: ${asset_prefix}" >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

content_dir="${tmp_dir}/content"
mkdir -p "$content_dir"
git archive --format=tar "origin/${branch}" "$asset_prefix" | tar -xf - -C "$tmp_dir"
cp -R "${tmp_dir}/${asset_prefix}"/. "$content_dir"/

namespace="$(jq -r '.thunderstore.namespace' <<<"$mod_json")"
token_key="$(echo "$namespace" | tr '[:lower:]-' '[:upper:]_')_THUNDER_TOKEN"
name="$(jq -r '.thunderstore.name' <<<"$mod_json")"
description="$(jq -r '.thunderstore.description[0:256]' <<<"$mod_json")"
owner="$(jq -r '.source.owner' <<<"$mod_json")"
repo="$(jq -r '.source.repo' <<<"$mod_json")"
readme_path="${ROOT_DIR}/$(jq -r '.package_files.readme' <<<"$mod_json")"
icon_path="${ROOT_DIR}/$(jq -r '.package_files.icon' <<<"$mod_json")"

readme_source_path="$(jq -r '.package_files.readme_source // "README.md"' <<<"$mod_json")"
readme_sync_enabled="$(jq -r '.package_files.sync_with_source_readme // true' <<<"$mod_json")"

readme_ref="v${VERSION}"
metadata_path="${content_dir}/_sync_metadata.json"
if [[ -f "$metadata_path" ]]; then
  candidate_ref="$(jq -r '.source.tag // empty' "$metadata_path")"
  if [[ -n "$candidate_ref" ]]; then
    readme_ref="$candidate_ref"
  fi
fi

if [[ ! -f "$readme_path" ]]; then
  echo "Readme file not found: $readme_path" >&2
  exit 1
fi
if [[ ! -f "$icon_path" ]]; then
  echo "Icon file not found: $icon_path" >&2
  exit 1
fi

manifest_path="${tmp_dir}/manifest.json"
readme_generated_path="${tmp_dir}/README.generated.md"

if [[ "$readme_sync_enabled" == "true" ]]; then
  if try_sync_readme "$owner" "$repo" "$readme_ref" "$readme_source_path" "$readme_generated_path"; then
    readme_path="$readme_generated_path"
    echo "Readme synced from source: ${owner}/${repo}@${readme_ref}:${readme_source_path}"
  else
    echo "Readme sync failed, fallback to local readme template: $readme_path"
  fi
fi

jq -n \
  --arg name "$name" \
  --arg version "$VERSION" \
  --arg website "https://github.com/${owner}/${repo}" \
  --arg desc "$description" \
  --argjson deps "$(jq -c '.thunderstore.dependencies' <<<"$mod_json")" \
  '{name:$name, version_number:$version, website_url:$website, description:$desc, dependencies:$deps}' > "$manifest_path"

package_stage="${tmp_dir}/package"
mkdir -p "$package_stage"
cp "$manifest_path" "${package_stage}/manifest.json"
cp "$readme_path" "${package_stage}/README.md"
cp "$icon_path" "${package_stage}/icon.png"
cp -R "$content_dir"/. "$package_stage"/

out_dir="${ROOT_DIR}/build/packages/${MOD_KEY}/${VERSION}"
mkdir -p "$out_dir"
zip_name="${namespace}-${name}-${VERSION}.zip"
zip_path="${out_dir}/${zip_name}"

(
  cd "$package_stage"
  zip -qr "$zip_path" .
)

echo "Built package: $zip_path"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "mod_key=${MOD_KEY}"
    echo "version=${VERSION}"
    echo "namespace=${namespace}"
    echo "thunder_token_key=${token_key}"
    echo "package_name=${name}"
    echo "package_path=${zip_path}"
    echo "manifest_path=${package_stage}/manifest.json"
    echo "readme_path=${package_stage}/README.md"
    echo "icon_path=${package_stage}/icon.png"
  } >> "$GITHUB_OUTPUT"
fi
