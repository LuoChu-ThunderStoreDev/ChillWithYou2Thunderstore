"""Phase 2: Assemble Thunderstore package zip from assets branch."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import get_mod
from .models import ModsFile
from .gh import (
    remote_branch_exists,
    list_versions_on_branch,
    get_raw_file,
    clone_branch_content,
)
from .readme_rewriter import rewrite_links
from .ci_output import CIOutput


def _token_key(namespace: str) -> str:
    """Derive the GitHub Secret token key from a Thunderstore namespace.

    Example: 'Small-tailqwq' -> 'SMALL_TAILQWQ_THUNDER_TOKEN'
    """
    return namespace.upper().replace("-", "_") + "_THUNDER_TOKEN"


def build_package(
    cfg: ModsFile,
    mod_key: str,
    version: str | None,
    ci: CIOutput,
) -> None:
    """Assemble a Thunderstore package zip from assets branch content.

    - Checks out the asset files for the given mod/version from the assets branch
    - Generates manifest.json from mods.json config
    - Syncs README from source repo (best-effort), falls back to template on failure
    - Assembles all files into a zip at build/packages/<mod_key>/<version>/
    - Writes GITHUB_OUTPUT for downstream workflow dispatch
    """
    mod = get_mod(cfg, mod_key, require_enabled=True)
    branch = f"assets/{mod_key}"

    if not remote_branch_exists(branch):
        print(f"Remote branch not found: {branch}", file=sys.stderr)
        raise SystemExit(1)

    if not version:
        versions = list_versions_on_branch(branch, mod_key)
        if not versions:
            print(f"No versions found under branch {branch}", file=sys.stderr)
            raise SystemExit(1)
        version = versions[-1]

    asset_prefix = f"assets/{mod_key}/{version}"

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        content_dir = tmp_dir / "content"
        content_dir.mkdir(parents=True)

        clone_branch_content(branch, asset_prefix, tmp_dir)
        extracted = tmp_dir / asset_prefix
        if extracted.exists():
            for item in extracted.iterdir():
                dest = content_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

        namespace = mod.thunderstore.namespace
        token_key = _token_key(namespace)
        name = mod.thunderstore.name
        # Truncate description to 256 Unicode characters (not bytes)
        description = mod.thunderstore.description[:256]
        owner = mod.source.owner
        repo = mod.source.repo
        readme_path = Path(mod.package_files.readme)
        icon_path = Path(mod.package_files.icon)
        readme_source_path = mod.package_files.readme_source
        readme_sync_enabled = mod.package_files.sync_with_source_readme

        # Determine ref for README sync: use source.tag from metadata if available
        readme_ref = f"v{version}"
        metadata_path = content_dir / "_sync_metadata.json"
        if metadata_path.exists():
            with open(metadata_path) as f:
                meta = json.load(f)
            if meta.get("source", {}).get("tag"):
                readme_ref = meta["source"]["tag"]

        if not readme_path.exists():
            print(f"Readme file not found: {readme_path}", file=sys.stderr)
            raise SystemExit(1)
        if not icon_path.exists():
            print(f"Icon file not found: {icon_path}", file=sys.stderr)
            raise SystemExit(1)

        effective_readme = readme_path
        if readme_sync_enabled:
            raw = get_raw_file(owner, repo, readme_ref, readme_source_path)
            if raw is not None:
                rewritten = rewrite_links(
                    raw, owner, repo, readme_ref, readme_source_path,
                )
                generated = tmp_dir / "README.generated.md"
                generated.write_text(rewritten, encoding="utf-8")
                effective_readme = generated
                print(
                    f"Readme synced from source: "
                    f"{owner}/{repo}@{readme_ref}:{readme_source_path}"
                )
            else:
                print(
                    f"Readme sync failed, fallback to local readme template: "
                    f"{readme_path}"
                )

        manifest = {
            "name": name,
            "version_number": version,
            "website_url": f"https://github.com/{owner}/{repo}",
            "description": description,
            "dependencies": mod.thunderstore.dependencies,
        }
        manifest_path = tmp_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        package_stage = tmp_dir / "package"
        package_stage.mkdir()
        shutil.copy2(manifest_path, package_stage / "manifest.json")
        shutil.copy2(effective_readme, package_stage / "README.md")
        shutil.copy2(icon_path, package_stage / "icon.png")
        for item in content_dir.iterdir():
            if item.name == "_sync_metadata.json":
                continue
            dest = package_stage / item.name
            if item.is_dir():
                if not dest.exists():
                    shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        out_dir = Path("build/packages") / mod_key / version
        out_dir.mkdir(parents=True, exist_ok=True)
        zip_name = f"{namespace}-{name}-{version}.zip"
        zip_path = out_dir / zip_name

        subprocess.run(
            ["zip", "-qr", str(zip_path), "."],
            cwd=package_stage,
            check=True,
            timeout=120,
        )

        print(f"Built package: {zip_path}")
        ci.write_outputs(
            mod_key=mod_key,
            version=version,
            namespace=namespace,
            thunder_token_key=token_key,
            package_name=name,
            package_path=str(zip_path),
            manifest_path=str(package_stage / "manifest.json"),
            readme_path=str(package_stage / "README.md"),
            icon_path=str(package_stage / "icon.png"),
        )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
