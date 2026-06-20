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
    clone_branch_content,
)
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
    - Reads pre-synced readme_rewrite (mandatory) and CHANGELOG.md (optional)
    - Assembles all files into a zip at build/packages/<mod_key>/<version>/
    - Writes GITHUB_OUTPUT for downstream workflow dispatch
    """
    mod = get_mod(cfg, mod_key, require_enabled=True)
    branch = f"assets/{mod_key}"

    if not remote_branch_exists(branch):
        print(f"Remote branch not found: {branch}", file=sys.stderr)
        raise SystemExit(1)

    if not version:
        versions = list_versions_on_branch(branch)
        if not versions:
            print(f"No versions found under branch {branch}", file=sys.stderr)
            raise SystemExit(1)
        version = versions[-1]

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        content_dir = tmp_dir / "content"
        content_dir.mkdir(parents=True)

        clone_branch_content(branch, version, tmp_dir)
        extracted = tmp_dir / version
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
        icon_path = Path(mod.package_files.icon)

        if not icon_path.exists():
            print(f"Icon file not found: {icon_path}", file=sys.stderr)
            raise SystemExit(1)

        # README is mandatory — must have been synced in phase 1
        readme_rewrite_path = content_dir / "readme_rewrite"
        if not readme_rewrite_path.exists():
            print(
                "readme_rewrite not found on asset branch — "
                "README sync must have failed or sync_readme is false",
                file=sys.stderr,
            )
            raise SystemExit(1)

        # CHANGELOG is optional
        changelog_path = content_dir / "CHANGELOG.md"

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
        # rename readme_rewrite → README.md on copy
        shutil.copy2(readme_rewrite_path, package_stage / "README.md")
        shutil.copy2(icon_path, package_stage / "icon.png")
        if changelog_path.exists():
            shutil.copy2(changelog_path, package_stage / "CHANGELOG.md")

        for item in content_dir.iterdir():
            excluded = {"_sync_metadata.json", "readme_origin", "readme_rewrite", "CHANGELOG.md"}
            if item.name in excluded:
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
            ["zip", "-qr", str(zip_path.resolve()), "."],
            cwd=package_stage,
            check=True,
            timeout=120,
        )

        # Copy manifest, readme, icon to out_dir so they survive tmp_dir cleanup
        shutil.copy2(package_stage / "manifest.json", out_dir / "manifest.json")
        shutil.copy2(package_stage / "README.md", out_dir / "README.md")
        shutil.copy2(package_stage / "icon.png", out_dir / "icon.png")

        print(f"Built package: {zip_path}")
        ci.write_outputs(
            mod_key=mod_key,
            version=version,
            namespace=namespace,
            thunder_token_key=token_key,
            package_name=name,
            package_path=str(zip_path),
            manifest_path=str(out_dir / "manifest.json"),
            readme_path=str(out_dir / "README.md"),
            icon_path=str(out_dir / "icon.png"),
        )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
