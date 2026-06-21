"""Phase 1: Download GitHub Release assets and push to assets branch."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import get_mod
from .models import ModsFile, ModConfig, AssetRule
from .gh import get_release, download_asset, push_to_branch, remote_branch_exists, list_versions_on_branch, list_all_releases, get_raw_file
from .readme_rewriter import rewrite_links
from .ci_output import CIOutput
from .thunderstore_api import ThunderstoreAPI


def _semver_from_tag(tag: str) -> str:
    """Extract strict X.Y.Z semver from a git tag. Raises ValueError on invalid tags."""
    t = tag.removeprefix("refs/tags/").removeprefix("v")
    if not re.match(r"^\d+\.\d+\.\d+$", t):
        raise ValueError(f"Invalid SemVer tag: {tag}")
    return t


def _match_glob(name: str, pattern: str) -> bool:
    """Shell-style glob matching using fnmatch."""
    return fnmatch.fnmatch(name, pattern)


def _write_skipped(mod_key: str, version: str, reason: str) -> None:
    """Record a skipped mod to SYNC_SKIPPED_FILE for orchestrator summary."""
    skipped_file = os.environ.get("SYNC_SKIPPED_FILE")
    if skipped_file:
        sp = Path(skipped_file)
        sp.parent.mkdir(parents=True, exist_ok=True)
        with open(sp, "a") as f:
            json.dump({"mod_key": mod_key, "version": version, "reason": reason}, f)
            f.write("\n")


def _write_summary(mod_key: str, version: str) -> None:
    """Record a synced mod to SYNC_SUMMARY_FILE for downstream dispatch."""
    summary_file = os.environ.get("SYNC_SUMMARY_FILE")
    if summary_file:
        sp = Path(summary_file)
        sp.parent.mkdir(parents=True, exist_ok=True)
        with open(sp, "a") as f:
            json.dump({"mod_key": mod_key, "version": version}, f)
            f.write("\n")


def _copy_file_with_target(
    src: Path, target: str, out_root: Path,
) -> None:
    """Copy src to out_root/target. If target ends with /, treat as dir and keep filename."""
    if target.endswith("/"):
        dst = out_root / target / src.name
    else:
        dst = out_root / target
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_file_preserve_path(
    src: Path, rel: str, out_root: Path,
) -> None:
    """Copy src to out_root/rel, keeping its relative path structure."""
    dst = out_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _process_rule(
    rule: AssetRule,
    release_assets: list[dict],
    dl_dir: Path,
    out_dir: Path,
) -> int:
    """Process a single asset rule against release assets. Returns number of files copied."""
    hit = 0
    matched = 0

    for asset in release_assets:
        name = asset["name"]
        url = asset["browser_download_url"]

        if not _match_glob(name, rule.matcher):
            continue

        matched += 1
        local_file = dl_dir / name
        download_asset(url, local_file)

        if rule.kind == "file":
            _copy_file_with_target(local_file, rule.target, out_dir)
            hit += 1
            continue

        if rule.kind == "zip":
            unzip_dir = dl_dir / f"unzipped_{Path(name).stem}"
            unzip_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(local_file, "r") as zf:
                zf.extractall(unzip_dir)

            consumed_patterns: list[str] = []
            for ex in (rule.extract or []):
                from_glob = ex.from_ or ""
                to_target = ex.to or ""
                consumed_patterns.append(from_glob)

                for m in unzip_dir.glob(from_glob):
                    if not m.is_file():
                        continue
                    _copy_file_with_target(m, to_target, out_dir)
                    hit += 1

            if rule.preserve_unmatched:
                default_excludes = [
                    "manifest.json",
                    "icon.png",
                    "README.md",
                    "readme.md",
                    "CHANGELOG.md",
                    "BepInEx/core/*",
                    "BepInEx/core/**",
                    "BepInEx/patchers/*",
                    "BepInEx/patchers/**",
                    "BepInEx/monomod/*",
                    "BepInEx/monomod/**",
                    "doorstop_config.ini",
                    "winhttp.dll",
                ]
                all_excludes = default_excludes + (rule.exclude or [])

                for f in unzip_dir.rglob("*"):
                    if not f.is_file():
                        continue
                    rel = str(f.relative_to(unzip_dir))

                    if any(fnmatch.fnmatch(rel, p) for p in consumed_patterns):
                        continue
                    if any(fnmatch.fnmatch(rel, p) for p in all_excludes):
                        continue

                    _copy_file_preserve_path(f, rel, out_dir)
                    hit += 1

    if matched == 0:
        print(f"No asset matched rule: {rule.matcher}")
    elif hit == 0:
        raise RuntimeError(
            f"Rule matched release asset, but no output files copied: {rule.matcher}"
        )

    return hit


def _sync_readme_and_changelog(
    out_dir: Path,
    owner: str,
    repo: str,
    tag_name: str,
    mod,
) -> None:
    """Fetch README (mandatory) and CHANGELOG (optional) from source repo.

    README is stored as readme_origin.md (raw) and readme_rewrite.md (links rewritten).
    CHANGELOG is stored as CHANGELOG.md. Both options are independent of each other.
    """
    pkg = mod.package_files

    # --- README (controlled by sync_readme) ---
    if pkg.sync_readme:
        readme_raw = get_raw_file(owner, repo, tag_name, pkg.readme_source)
        if readme_raw is None:
            raise RuntimeError(
                f"README is required for Thunderstore packages but not found at "
                f"{pkg.readme_source} in {owner}/{repo}@{tag_name}"
            )
        (out_dir / "readme_origin.md").write_text(readme_raw, encoding="utf-8")

        rewritten = rewrite_links(readme_raw, owner, repo, tag_name, pkg.readme_source)
        (out_dir / "readme_rewrite.md").write_text(rewritten, encoding="utf-8")
        print(f"README synced from {owner}/{repo}@{tag_name}:{pkg.readme_source}")

    # --- CHANGELOG (independently controlled, best-effort) ---
    if pkg.sync_changelog:
        changelog = get_raw_file(owner, repo, tag_name, pkg.changelog_source)
        if changelog is not None:
            (out_dir / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
            print(f"CHANGELOG synced from {owner}/{repo}@{tag_name}:{pkg.changelog_source}")
        else:
            print(f"CHANGELOG not found at {pkg.changelog_source}, skipping")


def _sync_one_version(
    mod: ModConfig,
    owner: str,
    repo: str,
    release,
    version: str,
    branch: str,
    dry_run: bool,
    commit_msg: str,
) -> None:
    """Download release assets, process rules, write metadata, sync README/CHANGELOG, push.

    Shared by sync_mod and sync_history. Raises on failure — callers decide
    whether to propagate or suppress.
    """
    tmp_root = Path(tempfile.mkdtemp())
    dl_dir = tmp_root / "downloads"
    out_dir = tmp_root / "out"
    dl_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    try:
        assets_raw = [
            {"name": a.name, "browser_download_url": a.browser_download_url}
            for a in release.assets
        ]

        for rule in mod.assets:
            _process_rule(rule, assets_raw, dl_dir, out_dir)

        if not any(out_dir.iterdir()):
            raise RuntimeError(
                f"No files collected for {mod.key} from release assets"
            )

        metadata = {
            "mod_key": mod.key,
            "source": {
                "owner": owner,
                "repo": repo,
                "tag": release.tag_name,
                "release_url": release.html_url,
            },
            "version": version,
            "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(out_dir / "_sync_metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        _sync_readme_and_changelog(out_dir, owner, repo, release.tag_name, mod)

        push_to_branch(branch, out_dir, mod.key, version, commit_msg, dry_run)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def sync_mod(
    cfg: ModsFile,
    mod_key: str,
    tag_override: str | None,
    dry_run: bool,
    _ci: CIOutput | None = None,   # deprecated, kept for backward compat
) -> tuple[str, str] | None:
    """Sync a single mod. Returns (mod_key, version) if the mod needs to enter
    the downstream build/publish matrix, or None if it should be skipped entirely.

    Pre-flight checks:
    1. If Thunderstore already has this exact version → return None (skip entirely)
    2. If assets branch already has this version → skip download+push, but still
       return (mod_key, version) so build/publish can proceed
    """
    mod = get_mod(cfg, mod_key, require_enabled=False)
    owner = mod.source.owner
    repo = mod.source.repo

    release = get_release(owner, repo, tag_override)
    version = _semver_from_tag(release.tag_name)
    tag_name = release.tag_name

    # --- Pre-flight check 1: Thunderstore ---
    ns = mod.thunderstore.namespace
    nm = mod.thunderstore.name
    api = ThunderstoreAPI()
    ts_pkg = api.check_package_exists(ns, nm)
    if ts_pkg is not None:
        ts_version = ts_pkg.get("latest", {}).get("version_number")
        if ts_version == version:
            reason = f"version {version} already published on Thunderstore ({ns}/{nm})"
            print(f"Skipping {mod_key}: {reason}")
            _write_skipped(mod_key, version, reason)
            return None

    # --- Pre-flight check 2: Assets branch ---
    branch = f"assets/{mod_key}"
    if remote_branch_exists(branch):
        existing = list_versions_on_branch(branch)
        if version in existing:
            print(f"Version {version} already on {branch}, skipping download+push")
            _write_summary(mod_key, version)
            return mod_key, version

    # --- Full sync ---
    print(f"Syncing {mod_key} from {owner}/{repo} tag {tag_name} -> version {version}")
    commit_msg = f"sync({mod_key}): {version} from {owner}/{repo}@{tag_name}"
    _sync_one_version(mod, owner, repo, release, version, branch, dry_run, commit_msg)

    _write_summary(mod_key, version)
    print(f"Synced {mod_key}@{version}")
    print(f"Source release: {release.html_url}")
    return mod_key, version


def sync_all(
    cfg: ModsFile,
    tag: str | None,
    dry_run: bool,
) -> list[tuple[str, str]]:
    """Sync all enabled mods. Returns list of (mod_key, version) for mods that
    need to enter the downstream build/publish matrix. Skipped mods are excluded."""
    results: list[tuple[str, str]] = []
    mods = [m for m in cfg.mods if m.enabled]
    if not mods:
        print("No enabled mods found")
        return results
    for mod in mods:
        try:
            result = sync_mod(cfg, mod.key, tag, dry_run)
            if result is not None:
                results.append(result)
        except Exception as e:
            print(f"Failed to sync {mod.key}: {e}")
    return results


def sync_history(
    cfg: ModsFile,
    mod_key: str | None,
    dry_run: bool,
    tag: str | None = None,
) -> dict[str, dict[str, list[str]]]:
    """Backfill all historical releases to the assets branch.

    Iterates ALL GitHub releases for each mod. Skips versions already
    present on the branch. Syncs the rest using current mods.json rules.

    If tag is provided, only that specific release tag is processed
    (useful for retrying a single failed release).

    Returns {mod_key: {"synced": ["1.0.0", ...], "skipped": ["1.1.0", ...]}}
    """
    results: dict[str, dict[str, list[str]]] = {}

    if mod_key:
        # Explicit --mod-key: get that specific mod regardless of enabled status
        mod = get_mod(cfg, mod_key, require_enabled=False)
        mods = [mod]
    else:
        mods = [m for m in cfg.mods if m.enabled]
    if not mods:
        print("No mods to backfill")
        return results

    for mod in mods:
        mk = mod.key
        owner = mod.source.owner
        repo = mod.source.repo
        branch = f"assets/{mk}"
        results[mk] = {"synced": [], "skipped": []}

        print(f"\n=== Backfilling {mk} ({owner}/{repo}) ===")

        # Fetch all release tags
        try:
            all_tags = list_all_releases(owner, repo)
        except Exception as e:
            print(f"Failed to list releases for {mk}: {e}")
            continue

        # Get versions already on the branch
        existing_versions: set[str] = set()
        if remote_branch_exists(branch):
            existing_versions = set(list_versions_on_branch(branch))

        for tag_name in all_tags:
            # If a specific tag was requested, skip everything else.
            # Normalize "v" prefix: GitHub tags are "v1.2.3" but users may pass "1.2.3".
            if tag is not None and tag_name.removeprefix("v") != tag.removeprefix("v"):
                continue
            # Filter to SemVer only
            try:
                version = _semver_from_tag(tag_name)
            except ValueError:
                print(f"  Skipping non-SemVer tag: {tag_name}")
                continue

            # Skip if already on branch
            if version in existing_versions:
                print(f"  {version} — already on branch, skipping")
                results[mk]["skipped"].append(version)
                continue

            # --- Sync this version ---
            print(f"  Syncing {tag_name} -> version {version}")
            try:
                release = get_release(owner, repo, tag_name)
                commit_msg = f"backfill({mk}): {version} from {owner}/{repo}@{tag_name}"
                _sync_one_version(mod, owner, repo, release, version, branch,
                                  dry_run, commit_msg)
                results[mk]["synced"].append(version)
                print(f"  Synced {mk}@{version}")
            except Exception as e:
                print(f"  Failed to sync {mk}@{version}: {e}")

    return results
