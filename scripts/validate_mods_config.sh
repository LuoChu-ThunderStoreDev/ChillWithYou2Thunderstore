#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${ROOT_DIR}/config/mods.json"

usage() {
  cat <<EOF
Usage: validate_mods_config.sh [--config <path>]
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

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config not found: $CONFIG_PATH" >&2
  exit 1
fi

if ! jq -e '.mods | type == "array" and length > 0' "$CONFIG_PATH" >/dev/null; then
  echo "Invalid config: .mods must be a non-empty array" >&2
  exit 1
fi

if ! jq -e '[.mods[].key] | length == (unique | length)' "$CONFIG_PATH" >/dev/null; then
  echo "Invalid config: duplicated mod key detected" >&2
  exit 1
fi

mapfile -t keys < <(jq -r '.mods[].key' "$CONFIG_PATH")

for key in "${keys[@]}"; do
  mod_json="$(jq -c --arg k "$key" '.mods[] | select(.key == $k)' "$CONFIG_PATH")"

  for field in '.enabled' '.source.owner' '.source.repo' '.assets' '.thunderstore.namespace' '.thunderstore.name' '.thunderstore.description' '.thunderstore.dependencies' '.package_files.readme' '.package_files.icon'; do
    if ! jq -e "$field" <<<"$mod_json" >/dev/null; then
      echo "Invalid config for mod ${key}: missing ${field}" >&2
      exit 1
    fi
  done

  if ! jq -e '.assets | type == "array" and length > 0' <<<"$mod_json" >/dev/null; then
    echo "Invalid config for mod ${key}: .assets must be a non-empty array" >&2
    exit 1
  fi

  if ! jq -e '.thunderstore.dependencies | type == "array"' <<<"$mod_json" >/dev/null; then
    echo "Invalid config for mod ${key}: .thunderstore.dependencies must be an array" >&2
    exit 1
  fi

  readme_rel="$(jq -r '.package_files.readme' <<<"$mod_json")"
  icon_rel="$(jq -r '.package_files.icon' <<<"$mod_json")"

  if [[ ! -f "${ROOT_DIR}/${readme_rel}" ]]; then
    echo "Invalid config for mod ${key}: readme file not found: ${readme_rel}" >&2
    exit 1
  fi

  if [[ ! -f "${ROOT_DIR}/${icon_rel}" ]]; then
    echo "Invalid config for mod ${key}: icon file not found: ${icon_rel}" >&2
    exit 1
  fi

  mapfile -t rules < <(jq -c '.assets[]' <<<"$mod_json")
  for rule in "${rules[@]}"; do
    kind="$(jq -r '.kind // ""' <<<"$rule")"
    matcher="$(jq -r '.matcher // ""' <<<"$rule")"

    if [[ -z "$matcher" ]]; then
      echo "Invalid config for mod ${key}: asset rule matcher is required" >&2
      exit 1
    fi

    if [[ "$kind" != "file" && "$kind" != "zip" ]]; then
      echo "Invalid config for mod ${key}: unsupported asset kind: ${kind}" >&2
      exit 1
    fi

    if [[ "$kind" == "file" ]]; then
      target="$(jq -r '.target // ""' <<<"$rule")"
      if [[ -z "$target" ]]; then
        echo "Invalid config for mod ${key}: file rule requires target" >&2
        exit 1
      fi
    fi

    if [[ "$kind" == "zip" ]]; then
      if ! jq -e '.extract | type == "array" and length > 0' <<<"$rule" >/dev/null; then
        echo "Invalid config for mod ${key}: zip rule requires non-empty extract array" >&2
        exit 1
      fi

      if ! jq -e 'all(.extract[]; (.from | type == "string" and length > 0) and (.to | type == "string" and length > 0))' <<<"$rule" >/dev/null; then
        echo "Invalid config for mod ${key}: zip extract entries require from/to" >&2
        exit 1
      fi
    fi
  done
done

echo "Config validation passed: ${CONFIG_PATH}"
