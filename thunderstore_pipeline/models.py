"""Pydantic models for mods.json schema and API responses."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceInfo(BaseModel):
    owner: str
    repo: str


class ExtractRule(BaseModel):
    from_: str | None = Field(default=None, alias="from")   # aliased from "from" in JSON
    to: str | None = None

    model_config = {"populate_by_name": True}


class AssetRule(BaseModel):
    matcher: str
    kind: Literal["file", "zip"]
    target: str | None = None
    extract: list[ExtractRule] | None = None
    preserve_unmatched: bool = False
    exclude: list[str] = []

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "AssetRule":
        if self.kind == "file" and not self.target:
            raise ValueError("file rule requires target")
        if self.kind == "zip" and not self.extract:
            raise ValueError("zip rule requires non-empty extract array")
        if self.kind == "zip":
            for ex in self.extract:
                if not ex.from_ or not ex.to:
                    raise ValueError("zip extract entries require from/to")
        return self


class ThunderstoreInfo(BaseModel):
    community: str
    namespace: str
    name: str
    description: str
    dependencies: list[str] = []
    has_nsfw_content: bool = False
    categories: list[str] = ["mods"]


class PackageFiles(BaseModel):
    icon: str
    readme_source: str = "README.md"
    sync_readme: bool = True
    sync_changelog: bool = True
    changelog_source: str = "CHANGELOG.md"


class ModConfig(BaseModel):
    key: str
    enabled: bool
    source: SourceInfo
    assets: list[AssetRule]
    thunderstore: ThunderstoreInfo
    package_files: PackageFiles


class ModsFile(BaseModel):
    mods: list[ModConfig]

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "ModsFile":
        seen: set[str] = set()
        dupes: set[str] = set()
        for m in self.mods:
            if m.key in seen:
                dupes.add(m.key)
            seen.add(m.key)
        if dupes:
            raise ValueError(f"Duplicate mod keys: {dupes}")
        return self


class ReleaseAsset(BaseModel):
    name: str
    browser_download_url: str


class ReleaseMeta(BaseModel):
    tag_name: str
    html_url: str
    assets: list[ReleaseAsset]
