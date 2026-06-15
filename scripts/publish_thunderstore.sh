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
