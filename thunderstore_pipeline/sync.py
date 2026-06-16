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
from .gh import get_release, download_asset, push_to_branch
from .ci_output import CIOutput


def _semver_from_tag(tag: str) -> str:
    """Extract strict X.Y.Z semver from a git tag. Raises ValueError on invalid tags."""
    t = tag.removeprefix("refs/tags/").removeprefix("v")
    if not re.match(r"^\d+\.\d+\.\d+$", t):
        raise ValueError(f"Invalid SemVer tag: {tag}")
    return t


def _match_glob(name: str, pattern: str) -> bool:
    """Shell-style glob matching using fnmatch."""
    return fnmatch.fnmatch(name, pattern)


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

                # Use Path.glob for matching; fnmatch semantics aligned
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


def sync_mod(
    cfg: ModsFile,
    mod_key: str,
    tag_override: str | None,
    dry_run: bool,
    ci: CIOutput,
) -> tuple[str, str]:
    """Sync a single mod: fetch release, download assets, push to assets branch.

    Returns (mod_key, version) tuple.
    """
    mod = get_mod(cfg, mod_key, require_enabled=True)
    owner = mod.source.owner
    repo = mod.source.repo

    release = get_release(owner, repo, tag_override)
    version = _semver_from_tag(release.tag_name)
    tag_name = release.tag_name
    html_url = release.html_url

    print(f"Syncing {mod_key} from {owner}/{repo} tag {tag_name} -> version {version}")

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
                f"No files collected for {mod_key} from release assets"
            )

        metadata = {
            "mod_key": mod_key,
            "source": {
                "owner": owner,
                "repo": repo,
                "tag": tag_name,
                "release_url": html_url,
            },
            "version": version,
            "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(out_dir / "_sync_metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        branch = f"assets/{mod_key}"
        commit_msg = f"sync({mod_key}): {version} from {owner}/{repo}@{tag_name}"
        push_to_branch(branch, out_dir, mod_key, version, commit_msg, dry_run)

        summary_file = os.environ.get("SYNC_SUMMARY_FILE")
        if summary_file:
            sp = Path(summary_file)
            sp.parent.mkdir(parents=True, exist_ok=True)
            with open(sp, "a") as f:
                json.dump({"mod_key": mod_key, "version": version}, f)
                f.write("\n")

        print(f"Synced {mod_key}@{version}")
        print(f"Source release: {html_url}")
        return mod_key, version

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def sync_all(
    cfg: ModsFile,
    tag: str | None,
    dry_run: bool,
    ci: CIOutput,
) -> list[tuple[str, str]]:
    """Sync all enabled mods. Errors are reported per-mod but do not halt the loop.

    Returns list of (mod_key, version) for successfully synced mods.
    """
    results: list[tuple[str, str]] = []
    mods = [m for m in cfg.mods if m.enabled]
    if not mods:
        print("No enabled mods found")
        return results
    for mod in mods:
        try:
            result = sync_mod(cfg, mod.key, tag, dry_run, ci)
            results.append(result)
        except Exception as e:
            print(f"Failed to sync {mod.key}: {e}")
    return results
