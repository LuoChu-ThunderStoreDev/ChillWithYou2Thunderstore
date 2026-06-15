#!/usr/bin/env bash
set -euo pipefail

API_BASE="${THUNDERSTORE_API_BASE:-https://thunderstore.io}"
AUTH_TOKEN="${THUNDERSTORE_AUTH_TOKEN:-}"
AUTH_SCHEME="${THUNDERSTORE_AUTH_SCHEME:-Bearer}"
MANIFEST_PATH=""
README_PATH=""
ICON_PATH=""
NAMESPACE=""

usage() {
  cat <<EOF
Usage: validate_thunderstore.sh --manifest <path> --readme <path> --icon <path> --namespace <team> [--auth-token <token>] [--auth-scheme <scheme>]
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

post_json() {
  local endpoint="$1"
  local body="$2"
  local body_file="$3"
  local stderr_file="$4"
  local headers=(-H "Content-Type: application/json")
  if [[ -n "$AUTH_TOKEN" ]]; then
    headers+=(-H "Authorization: ${AUTH_SCHEME} ${AUTH_TOKEN}")
  fi

  # Return HTTP status code while preserving body and stderr for diagnostics.
  curl -sS -o "$body_file" -w "%{http_code}" "${headers[@]}" -X POST "${API_BASE}${endpoint}" -d "$body" 2>"$stderr_file"
}

build_result_json() {
  local check_name="$1"
  local endpoint="$2"
  local curl_exit="$3"
  local http_status="$4"
  local body_file="$5"
  local stderr_file="$6"

  local body_json="null"
  if [[ -s "$body_file" ]] && jq -e . "$body_file" >/dev/null 2>&1; then
    body_json="$(cat "$body_file")"
  fi

  local stderr_json="null"
  if [[ -s "$stderr_file" ]]; then
    stderr_json="$(jq -Rs . < "$stderr_file")"
  fi

  jq -n \
    --arg check_name "$check_name" \
    --arg endpoint "$endpoint" \
    --argjson curl_exit "$curl_exit" \
    --argjson http_status "${http_status:-0}" \
    --argjson response "$body_json" \
    --argjson stderr "$stderr_json" \
    '{check:$check_name, endpoint:$endpoint, curl_exit:$curl_exit, http_status:$http_status, response:$response, stderr:$stderr}'
}

check_result_ok() {
  local result_file="$1"
  jq -e '(.curl_exit == 0) and (.http_status >= 200 and .http_status < 300) and (.response != null) and (.response.success == true)' "$result_file" >/dev/null
}

print_result_summary() {
  local result_file="$1"
  jq -r '"\(.check) status=\(.http_status) curl_exit=\(.curl_exit) success=\((.response.success // false) | tostring)"' "$result_file"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest)
      MANIFEST_PATH="$2"
      shift 2
      ;;
    --readme)
      README_PATH="$2"
      shift 2
      ;;
    --icon)
      ICON_PATH="$2"
      shift 2
      ;;
    --namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    --auth-token)
      AUTH_TOKEN="$2"
      shift 2
      ;;
    --auth-scheme)
      AUTH_SCHEME="$2"
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

need_cmd curl
need_cmd jq
need_cmd base64

for f in "$MANIFEST_PATH" "$README_PATH" "$ICON_PATH"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing validation file: $f" >&2
    exit 1
  fi
done

if [[ -z "$NAMESPACE" ]]; then
  echo "--namespace is required" >&2
  exit 1
fi

if [[ -n "$AUTH_TOKEN" ]]; then
  :
fi

manifest_data="$(base64 -w0 "$MANIFEST_PATH" | jq -R .)"
readme_data="$(base64 -w0 "$README_PATH" | jq -R .)"
icon_data="$(base64 -w0 "$ICON_PATH" | jq -R .)"

manifest_body="$(jq -n --arg namespace "$NAMESPACE" --argjson manifest_data "$manifest_data" '{namespace:$namespace, manifest_data:$manifest_data}')"
readme_body="$(jq -n --argjson readme_data "$readme_data" '{readme_data:$readme_data}')"
icon_body="$(jq -n --argjson icon_data "$icon_data" '{icon_data:$icon_data}')"

mkdir -p build/validation
mkdir -p build/validation/raw

run_check() {
  local check_name="$1"
  local endpoint="$2"
  local body="$3"

  local body_file="build/validation/raw/${check_name}.body"
  local stderr_file="build/validation/raw/${check_name}.stderr"
  local result_file="build/validation/${check_name}.json"

  set +e
  local http_status
  http_status="$(post_json "$endpoint" "$body" "$body_file" "$stderr_file")"
  local curl_exit=$?
  set -e

  build_result_json "$check_name" "$endpoint" "$curl_exit" "$http_status" "$body_file" "$stderr_file" > "$result_file"
}

run_check "manifest" "/api/experimental/submission/validate/manifest-v1/" "$manifest_body"
run_check "readme" "/api/experimental/submission/validate/readme/" "$readme_body"
run_check "icon" "/api/experimental/submission/validate/icon/" "$icon_body"

ok_manifest="false"
ok_readme="false"
ok_icon="false"

check_result_ok "build/validation/manifest.json" && ok_manifest="true"
check_result_ok "build/validation/readme.json" && ok_readme="true"
check_result_ok "build/validation/icon.json" && ok_icon="true"

echo "manifest success: ${ok_manifest}"
echo "readme success: ${ok_readme}"
echo "icon success: ${ok_icon}"

print_result_summary "build/validation/manifest.json"
print_result_summary "build/validation/readme.json"
print_result_summary "build/validation/icon.json"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "manifest_success=${ok_manifest}"
    echo "readme_success=${ok_readme}"
    echo "icon_success=${ok_icon}"
  } >> "$GITHUB_OUTPUT"
fi

if [[ "$ok_manifest" != "true" || "$ok_readme" != "true" || "$ok_icon" != "true" ]]; then
  if grep -qi '"code"[[:space:]]*:[[:space:]]*1010' build/validation/raw/*.body 2>/dev/null; then
    echo "WARNING: Detected upstream protection block (code 1010). Validate from a different network/IP or CI runner."
  fi

  if grep -q '"http_status": 401' build/validation/*.json 2>/dev/null; then
    echo "WARNING: Received HTTP 401 from Thunderstore API. Check namespace token mapping and auth scheme."
  fi

  echo "WARNING: Validation checks reported issues. Detailed results are in build/validation/*.json and raw payloads are in build/validation/raw/*.body"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "validation_warning=true" >> "$GITHUB_OUTPUT"
  fi
  exit 0
fi

echo "All Thunderstore validations passed"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "validation_warning=false" >> "$GITHUB_OUTPUT"
fi
