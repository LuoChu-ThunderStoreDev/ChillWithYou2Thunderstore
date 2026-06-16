"""Phase 3: Upload validated package to Thunderstore via chunked usermedia API."""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

from .config import get_mod
from .models import ModsFile
from .thunderstore_api import ThunderstoreAPI, ThunderstoreError
from .ci_output import CIOutput


def _token_key(namespace: str) -> str:
    return namespace.upper().replace("-", "_") + "_THUNDER_TOKEN"


def _generate_thunderstore_toml(
    namespace: str, name: str, version: str, description: str,
    owner: str, repo: str, has_nsfw: bool, dependencies: list[str],
    out_dir: Path,
) -> Path:
    deps_lines = "\n".join(f'    "{d}",' for d in dependencies)
    if deps_lines:
        deps_lines = deps_lines.rstrip(",")

    escaped_desc = description.replace("\\", "\\\\").replace('"', '\\"')
    toml = f"""[package]
namespace = "{namespace}"
name = "{name}"
versionNumber = "{version}"
description = "{escaped_desc}"
websiteUrl = "https://github.com/{owner}/{repo}"
containsNsfwContent = {str(has_nsfw).lower()}

[dependencies]
packages = [
{deps_lines}
]

[build]
icon = "icon.png"
readme = "README.md"
"""
    toml_path = out_dir / "thunderstore.toml"
    toml_path.write_text(toml)
    return toml_path


def _read_manifest_version(zip_path: Path) -> str | None:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            with zf.open("manifest.json") as f:
                manifest = json.load(f)
                return manifest.get("version_number")
    except Exception:
        return None


def publish_package(
    cfg: ModsFile,
    mod_key: str,
    version: str | None,
    package_zip: Path,
    dry_run: bool,
    ci: CIOutput,
) -> None:
    mod = get_mod(cfg, mod_key, require_enabled=True)

    if not version:
        version = _read_manifest_version(package_zip)
        if not version:
            print("Could not resolve version from package manifest. Pass --version.", file=sys.stderr)
            raise SystemExit(1)

    namespace = mod.thunderstore.namespace
    token_key = _token_key(namespace)
    name = mod.thunderstore.name
    description = mod.thunderstore.description[:256]
    owner = mod.source.owner
    repo = mod.source.repo
    community = mod.thunderstore.community
    has_nsfw = mod.thunderstore.has_nsfw_content
    dependencies = mod.thunderstore.dependencies
    categories = mod.thunderstore.categories

    api = ThunderstoreAPI()

    ci.group("Publish Pre-flight")
    print(f"  mod_key:     {mod_key}")
    print(f"  name:        {name}")
    print(f"  version:     {version}")
    print(f"  namespace:   {namespace}")
    print(f"  community:   {community}")
    print(f"  package_zip: {package_zip}")
    print(f"  dry_run:     {dry_run}")
    ci.endgroup()

    toml_dir = Path(tempfile.mkdtemp())
    toml_path = _generate_thunderstore_toml(
        namespace, name, version, description, owner, repo,
        has_nsfw, dependencies, toml_dir,
    )
    print(f"Generated thunderstore.toml")
    ci.write_summary(
        f"<details><summary>thunderstore.toml</summary>\n\n"
        f"```toml\n{toml_path.read_text()}\n```\n\n</details>"
    )

    if dry_run:
        print()
        print("DRY RUN — stopping before upload.")
        print(f"Would have published {name} v{version} to {namespace} on {community}.")
        ci.write_output("published", "false")
        return

    file_size = package_zip.stat().st_size
    print()
    print(f"Initiating upload for {name} ({file_size} bytes)...")

    try:
        init_data = api.initiate_upload(name, file_size)
    except ThunderstoreError as e:
        print(f"Initiate upload failed: {e}", file=sys.stderr)
        raise SystemExit(1)

    user_media = init_data.get("user_media", {})
    uuid = user_media.get("uuid", "")
    if not uuid:
        print(f"Initiate upload returned unexpected response: {init_data}", file=sys.stderr)
        raise SystemExit(1)

    upload_urls = init_data.get("upload_urls", [])
    print(f"Upload UUID: {uuid}")

    try:
        parts = api.upload_chunks(package_zip, upload_urls)
    except ThunderstoreError as e:
        print(f"Chunk upload failed — aborting upload {uuid}: {e}", file=sys.stderr)
        api.abort_upload(uuid)
        raise SystemExit(1)

    print("All chunks uploaded.")
    print("Finishing upload...")

    try:
        api.finish_upload(uuid, parts)
    except ThunderstoreError as e:
        print(f"Finish upload failed: {e}", file=sys.stderr)
        api.abort_upload(uuid)
        raise SystemExit(1)

    print("Upload finished successfully.")
    print("Submitting package...")

    try:
        api.submit_package(uuid, namespace, community, categories, has_nsfw)
    except ThunderstoreError as e:
        print(f"Submit failed: {e}", file=sys.stderr)
        raise SystemExit(1)

    package_url = f"{api.base_url}/package/{namespace}/{name}/{version}/"
    print()
    print(f"Published: {package_url}")

    ci.write_outputs(
        published="true",
        package_url=package_url,
        uuid=uuid,
        namespace=namespace,
        name=name,
        version=version,
        mod_key=mod_key,
    )

    ci.write_summary(f"""## Publish Summary

| Field | Value |
|-------|-------|
| mod_key | {mod_key} |
| name | {name} |
| version | {version} |
| namespace | {namespace} |
| community | {community} |
| url | [{package_url}]({package_url}) |
| uuid | {uuid} |
""")
