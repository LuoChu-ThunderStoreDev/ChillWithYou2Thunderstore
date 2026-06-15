#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${ROOT_DIR}/config/mods.json"

MOD_KEY=""
TAG_OVERRIDE=""
RUN_ALL="false"
DRY_RUN="${DRY_RUN:-false}"

usage() {
  cat <<EOF
Usage: sync_release_assets.sh [options]

Options:
  --config <path>        Path to mods.json
  --mod-key <key>        Target single mod key
  --tag <tag>            Override release tag
  --all                  Sync all enabled mods
  --dry-run              Build output but do not push branch
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

semver_from_tag() {
  local tag="$1"
  tag="${tag#refs/tags/}"
  tag="${tag#v}"
  if [[ ! "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid SemVer tag: $1" >&2
    return 1
  fi
  echo "$tag"
}

json_escape_file() {
  jq -Rs . < "$1"
}

download_release_json() {
  local owner="$1"
  local repo="$2"
  local tag="$3"
  local url=""

  if [[ -n "$tag" ]]; then
    url="https://api.github.com/repos/${owner}/${repo}/releases/tags/${tag}"
  else
    url="https://api.github.com/repos/${owner}/${repo}/releases/latest"
  fi

  local auth_header=()
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    auth_header=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
  fi

  curl -fsSL -H "Accept: application/vnd.github+json" "${auth_header[@]}" "$url"
}

copy_file_with_target() {
  local src="$1"
  local target="$2"
  local out_root="$3"
  local dst=""

  if [[ "$target" == */ ]]; then
    dst="${out_root}/${target}$(basename "$src")"
  else
    dst="${out_root}/${target}"
  fi

  mkdir -p "$(dirname "$dst")"
  cp -f "$src" "$dst"
}

copy_file_preserve_path() {
  local src="$1"
  local rel="$2"
  local out_root="$3"
  local dst="${out_root}/${rel}"
  mkdir -p "$(dirname "$dst")"
  cp -f "$src" "$dst"
}

path_matches_any_pattern() {
  local rel="$1"
  shift
  local patterns=("$@")
  local p
  for p in "${patterns[@]}"; do
    [[ -z "$p" ]] && continue
    if [[ "$rel" == $p ]]; then
      return 0
    fi
  done
  return 1
}

process_rule() {
  local rule_json="$1"
  local release_assets_json="$2"
  local dl_dir="$3"
  local out_dir="$4"

  local matcher kind
  matcher="$(jq -r '.matcher' <<<"$rule_json")"
  kind="$(jq -r '.kind' <<<"$rule_json")"

  mapfile -t asset_rows < <(jq -r '.[] | @base64' <<<"$release_assets_json")

  if [[ ${#asset_rows[@]} -eq 0 ]]; then
    echo "No release assets available for rule: $matcher"
    return 0
  fi

  local hit=0
  local matched=0
  for item in "${asset_rows[@]}"; do
    local row
    row="$(echo "$item" | base64 -d)"
    local name url
    name="$(jq -r '.name' <<<"$row")"

    # Use shell glob matching to align with mods.json matcher semantics.
    if [[ "$name" != $matcher ]]; then
      continue
    fi

    matched=1
    url="$(jq -r '.browser_download_url' <<<"$row")"

    local local_file="${dl_dir}/${name}"
    curl -fsSL "$url" -o "$local_file"

    if [[ "$kind" == "file" ]]; then
      local target
      target="$(jq -r '.target' <<<"$rule_json")"
      copy_file_with_target "$local_file" "$target" "$out_dir"
      hit=1
      continue
    fi

    if [[ "$kind" == "zip" ]]; then
      local unzip_dir="${dl_dir}/unzipped_${name%.*}"
      mkdir -p "$unzip_dir"
      unzip -q -o "$local_file" -d "$unzip_dir"

        local preserve_unmatched
        preserve_unmatched="$(jq -r '.preserve_unmatched // false' <<<"$rule_json")"

        local -a consumed_patterns=()
      mapfile -t extract_rules < <(jq -c '.extract[]?' <<<"$rule_json")
      for ex in "${extract_rules[@]}"; do
        local from_glob to_target
        from_glob="$(jq -r '.from' <<<"$ex")"
        to_target="$(jq -r '.to' <<<"$ex")"
          consumed_patterns+=("$from_glob")

        shopt -s nullglob
        local matches=("${unzip_dir}/"$from_glob)
        shopt -u nullglob

        if [[ ${#matches[@]} -eq 0 ]]; then
          continue
        fi

        for m in "${matches[@]}"; do
          copy_file_with_target "$m" "$to_target" "$out_dir"
          hit=1
        done
      done

      if [[ "$preserve_unmatched" == "true" ]]; then
        local -a default_excludes=(
          "manifest.json"
          "icon.png"
          "README.md"
          "readme.md"
          "CHANGELOG.md"
          "BepInEx/core/*"
          "BepInEx/core/**"
          "BepInEx/patchers/*"
          "BepInEx/patchers/**"
          "BepInEx/monomod/*"
          "BepInEx/monomod/**"
          "doorstop_config.ini"
          "winhttp.dll"
        )

        local -a custom_excludes=()
        mapfile -t custom_excludes < <(jq -r '.exclude[]?' <<<"$rule_json")

        while IFS= read -r -d '' f; do
          local rel
          rel="${f#${unzip_dir}/}"

          if path_matches_any_pattern "$rel" "${consumed_patterns[@]}"; then
            continue
          fi

          if path_matches_any_pattern "$rel" "${default_excludes[@]}"; then
            continue
          fi

          if path_matches_any_pattern "$rel" "${custom_excludes[@]}"; then
            continue
          fi

          copy_file_preserve_path "$f" "$rel" "$out_dir"
          hit=1
        done < <(find "$unzip_dir" -type f -print0)
      fi

      continue
    fi

    echo "Unsupported kind in rule: $kind" >&2
    return 1
  done

  if [[ "$matched" -eq 0 ]]; then
    echo "No asset matched rule: $matcher"
    return 0
  fi

  if [[ "$hit" -eq 0 ]]; then
    echo "Rule matched release asset, but no output files copied: $matcher" >&2
    return 1
  fi
}

push_to_assets_branch() {
  local mod_key="$1"
  local version="$2"
  local staged_dir="$3"
  local source_repo="$4"
  local source_tag="$5"
  local release_url="$6"

  local branch="assets/${mod_key}"
  local target_rel="assets/${mod_key}/${version}"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY RUN] skip pushing ${branch}:${target_rel}"
    return 0
  fi

  local worktree_dir
  worktree_dir="$(mktemp -d)"

  if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
    git fetch origin "$branch"
    git worktree add "$worktree_dir" "origin/${branch}" >/dev/null
    (
      cd "$worktree_dir"
      git checkout -B "$branch" >/dev/null
    )
  else
    git worktree add --detach "$worktree_dir" "${GITHUB_SHA:-HEAD}" >/dev/null
    (
      cd "$worktree_dir"
      git checkout --orphan "$branch" >/dev/null
      git rm -rf . >/dev/null 2>&1 || true
    )
  fi

  (
    cd "$worktree_dir"

    if [[ -d "$target_rel" ]] && [[ -n "$(find "$target_rel" -mindepth 1 -maxdepth 1 2>/dev/null || true)" ]]; then
      echo "Version already exists, skip push: ${target_rel}"
      exit 0
    fi

    mkdir -p "$target_rel"
    cp -R "$staged_dir"/. "$target_rel"/

    git add "$target_rel"

    if git diff --cached --quiet; then
      echo "No staged changes for ${mod_key}@${version}, skip commit"
      exit 0
    fi

    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

    git commit -m "sync(${mod_key}): ${version} from ${source_repo}@${source_tag}" >/dev/null
    git push origin "$branch" >/dev/null

    # Record synced mod for downstream workflow dispatch
    if [[ -n "${SYNC_SUMMARY_FILE:-}" ]]; then
      mkdir -p "$(dirname "$SYNC_SUMMARY_FILE")"
      echo "{\"mod_key\":\"${mod_key}\",\"version\":\"${version}\"}" >> "$SYNC_SUMMARY_FILE"
    fi
  )

  git worktree remove "$worktree_dir" --force >/dev/null
  echo "Pushed ${mod_key}@${version} to ${branch}"
  echo "Source release: ${release_url}"
}

sync_one_mod() {
  local mod_key="$1"

  local mod_json
  mod_json="$(jq -c --arg k "$mod_key" '.mods[] | select(.key == $k and .enabled == true)' "$CONFIG_PATH")"
  if [[ -z "$mod_json" ]]; then
    echo "Mod key not found or disabled: $mod_key" >&2
    return 1
  fi

  local owner repo
  owner="$(jq -r '.source.owner' <<<"$mod_json")"
  repo="$(jq -r '.source.repo' <<<"$mod_json")"

  local release_json
  release_json="$(download_release_json "$owner" "$repo" "$TAG_OVERRIDE")"

  local tag_name html_url
  tag_name="$(jq -r '.tag_name' <<<"$release_json")"
  html_url="$(jq -r '.html_url' <<<"$release_json")"

  local version
  version="$(semver_from_tag "$tag_name")"

  echo "Syncing ${mod_key} from ${owner}/${repo} tag ${tag_name} -> version ${version}"

  local tmp_root dl_dir out_dir
  tmp_root="$(mktemp -d)"
  dl_dir="${tmp_root}/downloads"
  out_dir="${tmp_root}/out"
  mkdir -p "$dl_dir" "$out_dir"

  local assets_json
  assets_json="$(jq -c '.assets' <<<"$release_json")"

  mapfile -t rules < <(jq -c '.assets[]' <<<"$mod_json")
  for rule in "${rules[@]}"; do
    process_rule "$rule" "$assets_json" "$dl_dir" "$out_dir"
  done

  if [[ -z "$(find "$out_dir" -type f -print -quit)" ]]; then
    echo "No files collected for ${mod_key} from release assets. Please check mods.json rules." >&2
    return 1
  fi

  local metadata_file="${out_dir}/_sync_metadata.json"
  jq -n \
    --arg mod_key "$mod_key" \
    --arg owner "$owner" \
    --arg repo "$repo" \
    --arg tag "$tag_name" \
    --arg version "$version" \
    --arg release_url "$html_url" \
    --arg synced_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{mod_key:$mod_key, source:{owner:$owner,repo:$repo,tag:$tag,release_url:$release_url}, version:$version, synced_at:$synced_at}' > "$metadata_file"

  push_to_assets_branch "$mod_key" "$version" "$out_dir" "${owner}/${repo}" "$tag_name" "$html_url"

  rm -rf "$tmp_root"
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
    --tag)
      TAG_OVERRIDE="$2"
      shift 2
      ;;
    --all)
      RUN_ALL="true"
      shift
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
need_cmd git
need_cmd unzip

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config not found: $CONFIG_PATH" >&2
  exit 1
fi

bash "${ROOT_DIR}/scripts/validate_mods_config.sh" --config "$CONFIG_PATH"

if [[ "$RUN_ALL" == "true" ]]; then
  mapfile -t keys < <(jq -r '.mods[] | select(.enabled == true) | .key' "$CONFIG_PATH")
  for k in "${keys[@]}"; do
    sync_one_mod "$k"
  done
  exit 0
fi

if [[ -z "$MOD_KEY" ]]; then
  if [[ "${GITHUB_EVENT_NAME:-}" == "schedule" ]]; then
    mapfile -t keys < <(jq -r '.mods[] | select(.enabled == true) | .key' "$CONFIG_PATH")
    for k in "${keys[@]}"; do
      sync_one_mod "$k"
    done
    exit 0
  fi

  echo "--mod-key is required unless --all is provided" >&2
  exit 1
fi

sync_one_mod "$MOD_KEY"
