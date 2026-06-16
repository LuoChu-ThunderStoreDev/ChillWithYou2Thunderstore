"""Load and validate mods.json configuration."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .models import ModsFile, ModConfig


ROOT_DIR = Path(__file__).resolve().parent.parent


def find_config_path(config_arg: str | None = None) -> Path:
    if config_arg:
        return Path(config_arg)
    env_path = os.environ.get("PIPELINE_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return ROOT_DIR / "config" / "mods.json"


def load_config(config_path: Path | None = None) -> ModsFile:
    if config_path is None:
        config_path = find_config_path()
    try:
        with open(config_path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"Config not found: {config_path}", file=sys.stderr)
        raise SystemExit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in config: {e}", file=sys.stderr)
        raise SystemExit(1)
    try:
        return ModsFile(**raw)
    except Exception as e:
        print(f"Config validation failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def get_mod(config: ModsFile, mod_key: str, require_enabled: bool = False) -> ModConfig:
    for mod in config.mods:
        if mod.key == mod_key:
            if require_enabled and not mod.enabled:
                print(f"Mod is disabled: {mod_key}", file=sys.stderr)
                raise SystemExit(1)
            return mod
    print(f"Mod key not found: {mod_key}", file=sys.stderr)
    raise SystemExit(1)


def get_enabled_mods(config: ModsFile) -> list[ModConfig]:
    return [m for m in config.mods if m.enabled]
