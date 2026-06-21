# thunderstore_pipeline/cli.py
"""Typer CLI entry point with subcommands for each pipeline phase."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from .config import load_config
from .ci_output import CIOutput

app = typer.Typer(
    name="thunderstore-pipeline",
    help="CI pipeline for converting GitHub Releases to Thunderstore packages",
    no_args_is_help=True,
)


@app.command()
def config_check(
    config: Annotated[Optional[Path], typer.Option("--config")] = None,
) -> None:
    """Validate config/mods.json schema and content."""
    try:
        cfg = load_config(config)
        print(f"Config validation passed: {len(cfg.mods)} mod(s)")
        for m in cfg.mods:
            print(f"  - {m.key} (enabled={m.enabled}, assets={len(m.assets)})")
    except SystemExit as e:
        raise typer.Exit(code=e.code)


@app.command()
def sync(
    all: Annotated[bool, typer.Option("--all")] = False,
    mod_key: Annotated[Optional[str], typer.Option("--mod-key")] = None,
    tag: Annotated[Optional[str], typer.Option("--tag")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Download GitHub Release assets and push to assets branch."""
    from .sync import sync_mod, sync_all

    cfg = load_config()
    ci = CIOutput()
    if all:
        sync_all(cfg, tag, dry_run, ci)
    elif mod_key:
        sync_mod(cfg, mod_key, tag, dry_run, ci)
    elif os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        sync_all(cfg, tag, dry_run, ci)
    else:
        print("Error: --mod-key or --all is required", file=sys.stderr)
        raise typer.Exit(code=1)


@app.command()
def build(
    mod_key: Annotated[str, typer.Option("--mod-key", help="Target mod key")],
    version: Annotated[Optional[str], typer.Option("--version")] = None,
) -> None:
    """Assemble a Thunderstore-compatible package zip from assets branch."""
    from .build import build_package

    cfg = load_config()
    ci = CIOutput()
    build_package(cfg, mod_key, version, ci)


@app.command()
def validate(
    manifest: Annotated[Path, typer.Option("--manifest")],
    readme: Annotated[Path, typer.Option("--readme")],
    icon: Annotated[Path, typer.Option("--icon")],
    namespace: Annotated[str, typer.Option("--namespace")],
    auth_token: Annotated[Optional[str], typer.Option("--auth-token")] = None,
    auth_scheme: Annotated[Optional[str], typer.Option("--auth-scheme")] = None,
) -> None:
    """Validate manifest, readme, icon against Thunderstore API."""
    from .validate import validate_package

    ci = CIOutput()
    validate_package(manifest, readme, icon, namespace, ci, auth_token, auth_scheme)


@app.command()
def publish(
    mod_key: Annotated[str, typer.Option("--mod-key")],
    package_zip: Annotated[Path, typer.Option("--package-zip")],
    version: Annotated[Optional[str], typer.Option("--version")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Upload a validated package to Thunderstore via chunked upload API."""
    from .publish import publish_package

    if not package_zip.exists():
        print(f"Package zip not found: {package_zip}", file=sys.stderr)
        raise typer.Exit(code=1)
    cfg = load_config()
    ci = CIOutput()
    publish_package(cfg, mod_key, version, package_zip, dry_run, ci)


@app.command()
def backfill(
    all: Annotated[bool, typer.Option("--all")] = False,
    mod_key: Annotated[Optional[str], typer.Option("--mod-key")] = None,
    tag: Annotated[Optional[str], typer.Option("--tag")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Backfill all historical releases to the assets branch.

    Manual trigger only — no schedule, no call from orchestrator.
    Iterates ALL GitHub releases for the mod(s) and syncs missing versions.
    Use --tag to backfill a single specific release version.
    """
    from .sync import sync_history

    if not all and not mod_key:
        print("Error: --mod-key or --all is required", file=sys.stderr)
        raise typer.Exit(code=1)

    cfg = load_config()
    results = sync_history(cfg, mod_key if mod_key else None, dry_run, tag)

    for mk, r in results.items():
        print(f"\n{mk}: synced={r['synced']} skipped={r['skipped']}")

    summary_file = os.environ.get("BACKFILL_SUMMARY_FILE")
    if summary_file:
        Path(summary_file).write_text(json.dumps(results, indent=2), encoding="utf-8")
