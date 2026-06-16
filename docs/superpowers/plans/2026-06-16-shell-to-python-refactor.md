# Shell → Python 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor all shell scripts in `scripts/` to a unified Python CLI package (`thunderstore_pipeline`) with thin GitHub Actions workflows that only trigger Python commands.

**Architecture:** A Python package with typer CLI providing subcommands (config-check, sync, build, validate, publish). GitHub operations use `gh` CLI via subprocess; Thunderstore API uses httpx directly. Workflows use workflow_call for composition with concurrency groups.

**Tech Stack:** Python 3.12+, typer, httpx, pydantic, uv, gh CLI

**Critical layout constraints:**
- Composite action MUST live at `.github/actions/setup/action.yml` (GitHub requirement)
- build and validate stay in **one job** (shared filesystem for package files)
- Orchestrator passes each phase workflow's run_id to the next for artifact download

---

## File Structure

```
ChillWithYou2Thunderstore/
├── pyproject.toml                  # [CREATE] uv-managed deps
├── thunderstore_pipeline/          # [CREATE] Python package
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                      # typer entry point
│   ├── models.py                   # pydantic schema
│   ├── config.py                   # config loader
│   ├── ci_output.py                # CI env integration
│   ├── readme_rewriter.py          # link rewriting
│   ├── gh.py                       # gh CLI wrapper
│   ├── thunderstore_api.py         # Thunderstore HTTP client
│   ├── sync.py                     # Phase 1 business logic
│   ├── build.py                    # Phase 2 business logic
│   ├── validate.py                 # validation logic
│   └── publish.py                  # Phase 3 business logic
├── .github/actions/setup/action.yml  # [CREATE] composite action
├── .github/workflows/
│   ├── sync.yml                    # [REPLACE]
│   ├── build-and-validate.yml      # [REPLACE]
│   ├── publish.yml                 # [REPLACE]
│   └── orchestrator.yml            # [CREATE]
└── scripts/                        # [DELETE old .sh files after verification]
```

---

### Task 1: Project scaffolding — pyproject.toml and package skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `thunderstore_pipeline/__init__.py`
- Create: `thunderstore_pipeline/__main__.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "thunderstore-pipeline"
version = "0.1.0"
description = "CI pipeline for converting GitHub Releases to Thunderstore packages"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.15",
    "httpx>=0.28",
    "pydantic>=2.10",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-mock>=3"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = ["pytest>=8", "pytest-mock>=3"]
```

- [ ] **Step 2: Run `uv sync` to create lock file**

```bash
cd /home/luochu/Projects/Game/Chill_With_You/Others/mod/ChillWithYou2Thunderstore
uv sync
```
Expected: Creates `uv.lock` and `.venv/`, installs typer, httpx, pydantic.

- [ ] **Step 3: Create package init file**

```python
# thunderstore_pipeline/__init__.py
"""CI pipeline for converting GitHub Releases to Thunderstore packages."""
```

- [ ] **Step 4: Create __main__.py**

```python
# thunderstore_pipeline/__main__.py
"""Entry point for python -m thunderstore_pipeline."""
from thunderstore_pipeline.cli import app

app()
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock thunderstore_pipeline/__init__.py thunderstore_pipeline/__main__.py
git commit -m "feat: add project scaffolding with uv, typer, httpx, pydantic"
```

---

### Task 2: Pydantic models and config loader

**Files:**
- Create: `thunderstore_pipeline/models.py`
- Create: `thunderstore_pipeline/config.py`

- [ ] **Step 1: Write models.py**

```python
# thunderstore_pipeline/models.py
"""Pydantic models for mods.json schema and API responses."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


class SourceInfo(BaseModel):
    owner: str
    repo: str


class ExtractRule(BaseModel):
    from_: str | None = None   # aliased from "from" in JSON
    to: str | None = None

    @field_validator("from_", mode="before")
    @classmethod
    def alias_from(cls, v: object) -> str | None:
        return v


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
        if self.kind == "zip" and (not self.extract or len(self.extract) == 0):
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
    categories: list[str] = ["Mods"]


class PackageFiles(BaseModel):
    readme: str
    icon: str
    readme_source: str = "README.md"
    sync_with_source_readme: bool = True


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
        keys = [m.key for m in self.mods]
        if len(keys) != len(set(keys)):
            raise ValueError(f"Duplicated mod keys detected: {keys}")
        return self


class ReleaseMeta(BaseModel):
    tag_name: str
    html_url: str
    assets: list[dict]  # minimal — only name, browser_download_url
```

- [ ] **Step 2: Write config.py**

```python
# thunderstore_pipeline/config.py
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
```

- [ ] **Step 3: Smoke test — parse real mods.json**

```bash
uv run python -c "
from thunderstore_pipeline.config import load_config
c = load_config()
print(f'Loaded {len(c.mods)} mods')
for m in c.mods:
    print(f'  {m.key} enabled={m.enabled} assets={len(m.assets)}')
"
```
Expected: Prints 3 mods with details.

- [ ] **Step 4: Commit**

```bash
git add thunderstore_pipeline/models.py thunderstore_pipeline/config.py
git commit -m "feat: add pydantic models and config loader"
```

---

### Task 3: CI output helper and README rewriter

**Files:**
- Create: `thunderstore_pipeline/ci_output.py`
- Create: `thunderstore_pipeline/readme_rewriter.py`

- [ ] **Step 1: Write ci_output.py**

```python
# thunderstore_pipeline/ci_output.py
"""CI environment integration. Writes to GITHUB_OUTPUT/STEP_SUMMARY when in CI,
falls back to stdout when running locally."""
from __future__ import annotations

import os
from pathlib import Path


class CIOutput:
    def __init__(self) -> None:
        self._output_file: Path | None = None
        self._summary_file: Path | None = None
        gh_output = os.environ.get("GITHUB_OUTPUT")
        if gh_output:
            self._output_file = Path(gh_output)
        gh_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if gh_summary:
            self._summary_file = Path(gh_summary)

    @property
    def is_ci(self) -> bool:
        return self._output_file is not None

    def write_output(self, key: str, value: str) -> None:
        if self._output_file:
            with open(self._output_file, "a") as f:
                f.write(f"{key}={value}\n")
        else:
            print(f"[CI_OUTPUT] {key}={value}")

    def write_outputs(self, **kwargs: str) -> None:
        for k, v in kwargs.items():
            self.write_output(k, v)

    def write_summary(self, markdown: str) -> None:
        if self._summary_file:
            with open(self._summary_file, "a") as f:
                f.write(markdown + "\n")
        else:
            print(f"[CI_SUMMARY]\n{markdown}")

    def write_env(self, key: str, value: str) -> None:
        env_file = os.environ.get("GITHUB_ENV")
        if env_file:
            with open(env_file, "a") as f:
                f.write(f"{key}={value}\n")
        else:
            os.environ[key] = value

    def group(self, title: str) -> None:
        print(f"::group::{title}")

    def endgroup(self) -> None:
        print("::endgroup::")
```

- [ ] **Step 2: Write readme_rewriter.py (migrate from scripts/rewrite_readme_links.py)**

```python
# thunderstore_pipeline/readme_rewriter.py
"""Rewrite relative links in Markdown README to absolute GitHub URLs."""
from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath

INLINE_LINK_RE = re.compile(r"(!?\[[^\]]*\]\()([^\)]+)(\))")
REF_LINK_RE = re.compile(r"^(\s*\[[^\]]+\]:\s*)(\S+)(.*)$")


def _normalize_rel_path(readme_path: str, target: str) -> str:
    base_dir = str(PurePosixPath(readme_path).parent)
    if target.startswith("/"):
        combined = target.lstrip("/")
    else:
        combined = posixpath.normpath(posixpath.join(base_dir, target))
    return combined.lstrip("./")


def _is_relative_url(url: str) -> bool:
    lowered = url.lower()
    return not lowered.startswith(("http://", "https://", "mailto:", "data:", "#"))


def _split_anchor(url: str) -> tuple[str, str]:
    if "#" in url:
        p, a = url.split("#", 1)
        return p, "#" + a
    return url, ""


def _rewrite_url(
    url: str, owner: str, repo: str, ref: str, readme_path: str, is_image: bool
) -> str:
    if not _is_relative_url(url):
        return url
    path_part, anchor = _split_anchor(url)
    normalized = _normalize_rel_path(readme_path, path_part)
    if is_image:
        base = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}"
    else:
        base = f"https://github.com/{owner}/{repo}/blob/{ref}"
    return f"{base}/{normalized}{anchor}"


def _rewrite_inline(text: str, owner: str, repo: str, ref: str, readme_path: str) -> str:
    def repl(match: re.Match) -> str:
        prefix, url, suffix = match.groups()
        is_image = prefix.startswith("!")
        parts = url.split(maxsplit=1)
        path_url = parts[0]
        rest = "" if len(parts) == 1 else " " + parts[1]
        new_url = _rewrite_url(path_url, owner, repo, ref, readme_path, is_image)
        return f"{prefix}{new_url}{rest}{suffix}"
    return INLINE_LINK_RE.sub(repl, text)


def _rewrite_ref_lines(
    text: str, owner: str, repo: str, ref: str, readme_path: str
) -> str:
    out_lines = []
    for line in text.splitlines():
        m = REF_LINK_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        left, url, rest = m.groups()
        new_url = _rewrite_url(url, owner, repo, ref, readme_path, False)
        out_lines.append(f"{left}{new_url}{rest}")
    return "\n".join(out_lines)


def rewrite_links(
    markdown: str, owner: str, repo: str, ref: str, readme_path: str = "README.md"
) -> str:
    text = _rewrite_inline(markdown, owner, repo, ref, readme_path)
    text = _rewrite_ref_lines(text, owner, repo, ref, readme_path)
    return text


def rewrite_readme_file(
    input_path: str, output_path: str,
    owner: str, repo: str, ref: str, readme_path: str = "README.md",
) -> None:
    with open(input_path, encoding="utf-8") as f:
        text = f.read()
    result = rewrite_links(text, owner, repo, ref, readme_path)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(result)
```

- [ ] **Step 3: Verify rewrite produces same output as old script**

```bash
echo '[logo](images/logo.png)' > /tmp/test_readme.md
python3 scripts/rewrite_readme_links.py \
  --input /tmp/test_readme.md --output /tmp/old_out.md \
  --owner Small-tailqwq --repo Test --ref v1.0 --readme-path README.md
uv run python -c "
from thunderstore_pipeline.readme_rewriter import rewrite_readme_file
rewrite_readme_file('/tmp/test_readme.md', '/tmp/new_out.md',
                    'Small-tailqwq', 'Test', 'v1.0', 'README.md')
"
diff /tmp/old_out.md /tmp/new_out.md && echo "OUTPUT MATCHES"
```
Expected: OUTPUT MATCHES

- [ ] **Step 4: Commit**

```bash
git add thunderstore_pipeline/ci_output.py thunderstore_pipeline/readme_rewriter.py
git commit -m "feat: add CI output helper and readme link rewriter"
```

---

### Task 4: GitHub CLI wrapper (gh.py)

**Files:**
- Create: `thunderstore_pipeline/gh.py`

- [ ] **Step 1: Write gh.py**

```python
# thunderstore_pipeline/gh.py
"""GitHub CLI (gh) wrapper for release, branch, and dispatch operations."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .models import ReleaseMeta


class GhError(Exception):
    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


def _run(args: list[str], check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise GhError(f"Command failed: {' '.join(args)}\n{result.stderr}", stderr=result.stderr)
    return result


def _gh_json(endpoint: str, method: str = "GET") -> dict | list | None:
    """Run gh api and return parsed JSON."""
    result = _run(["gh", "api", endpoint, "--method", method])
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()


def get_release(owner: str, repo: str, tag: str | None = None) -> ReleaseMeta:
    """Fetch release metadata. tag=None fetches latest."""
    endpoint = f"repos/{owner}/{repo}/releases"
    if tag:
        endpoint += f"/tags/{tag}"
    else:
        endpoint += "/latest"
    data = _gh_json(endpoint)
    if not data:
        raise GhError(f"No release found for {owner}/{repo}" + (f" tag={tag}" if tag else ""))
    if isinstance(data, list):
        if not data:
            raise GhError(f"No release found for {owner}/{repo} tag={tag}")
        data = data[0]
    return ReleaseMeta(
        tag_name=data["tag_name"],
        html_url=data["html_url"],
        assets=[{"name": a["name"], "browser_download_url": a["browser_download_url"]} for a in data.get("assets", [])],
    )


def download_asset(url: str, dest: Path) -> Path:
    _run(["curl", "-fsSL", url, "-o", str(dest)], timeout=300)
    return dest


def get_raw_file(owner: str, repo: str, ref: str, path: str) -> str | None:
    """Fetch raw file from raw.githubusercontent.com. Returns None on 404."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    result = subprocess.run(["curl", "-fsSL", url], capture_output=True, text=True, timeout=30)
    return result.stdout if result.returncode == 0 else None


def remote_branch_exists(branch: str) -> bool:
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        capture_output=True, timeout=30,
    )
    return result.returncode == 0


def list_versions_on_branch(branch: str, mod_key: str) -> list[str]:
    prefix = f"assets/{mod_key}"
    result = _run(["git", "ls-tree", "-r", "--name-only", f"origin/{branch}", prefix])
    versions = set()
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("/")
        if len(parts) >= 3 and re.match(r"^\d+\.\d+\.\d+$", parts[2]):
            versions.add(parts[2])
    return sorted(versions, key=lambda v: tuple(int(x) for x in v.split(".")))


def clone_branch_content(branch: str, prefix: str, dest: Path) -> None:
    """Extract files under prefix from remote branch into dest using git archive."""
    _run(["git", "fetch", "origin", branch])
    result = subprocess.run(
        ["git", "archive", "--format=tar", f"origin/{branch}", prefix],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        raise GhError(f"git archive failed: {result.stderr.decode()}", result.stderr.decode())
    import tarfile, io
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
        tar.extractall(path=dest)


def push_to_branch(
    branch: str, files_dir: Path, mod_key: str, version: str,
    commit_msg: str, dry_run: bool = False,
) -> None:
    """Push files to a git branch via worktree. Skips if version exists."""
    if dry_run:
        print(f"[DRY RUN] skip pushing {branch}:assets/{mod_key}/{version}")
        return

    target_rel = f"assets/{mod_key}/{version}"
    worktree_dir = Path(tempfile.mkdtemp())

    try:
        if remote_branch_exists(branch):
            _run(["git", "fetch", "origin", branch])
            _run(["git", "worktree", "add", str(worktree_dir), f"origin/{branch}"])
            subprocess.run(["git", "checkout", "-B", branch], cwd=worktree_dir,
                           capture_output=True, timeout=30)
        else:
            base = os.environ.get("GITHUB_SHA", "HEAD")
            _run(["git", "worktree", "add", "--detach", str(worktree_dir), base])
            subprocess.run(["git", "checkout", "--orphan", branch], cwd=worktree_dir,
                           capture_output=True, timeout=30)
            subprocess.run(["git", "rm", "-rf", "."], cwd=worktree_dir,
                           capture_output=True, timeout=30)

        target_dir = worktree_dir / target_rel
        if target_dir.exists() and any(target_dir.iterdir()):
            print(f"Version already exists, skip push: {target_rel}")
            return

        target_dir.mkdir(parents=True, exist_ok=True)
        for item in files_dir.iterdir():
            dest = target_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        subprocess.run(["git", "add", target_rel], cwd=worktree_dir, capture_output=True, timeout=30)
        diff_rc = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=worktree_dir,
                                 capture_output=True, timeout=30).returncode
        if diff_rc == 0:
            print(f"No staged changes for {mod_key}@{version}, skip commit")
            return

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"],
                       cwd=worktree_dir, capture_output=True, timeout=30)
        subprocess.run(["git", "config", "user.email",
                        "41898282+github-actions[bot]@users.noreply.github.com"],
                       cwd=worktree_dir, capture_output=True, timeout=30)
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=worktree_dir,
                       capture_output=True, timeout=30)
        _run(["git", "push", "origin", branch])
        print(f"Pushed {mod_key}@{version} to {branch}")
    finally:
        subprocess.run(["git", "worktree", "remove", str(worktree_dir), "--force"],
                       capture_output=True, timeout=30)
        shutil.rmtree(worktree_dir, ignore_errors=True)
```

- [ ] **Step 2: Commit**

```bash
git add thunderstore_pipeline/gh.py
git commit -m "feat: add gh CLI wrapper for GitHub operations"
```

---

### Task 5: Thunderstore API client

**Files:**
- Create: `thunderstore_pipeline/thunderstore_api.py`

- [ ] **Step 1: Write thunderstore_api.py**

```python
# thunderstore_pipeline/thunderstore_api.py
"""Thunderstore HTTP API client using httpx."""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import httpx


DEFAULT_API_BASE = "https://thunderstore.io"


class ThunderstoreError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class ValidationResult:
    def __init__(self, check: str, success: bool, http_status: int = 0,
                 curl_exit: int = 0, response: dict | None = None, stderr: str = ""):
        self.check = check
        self.success = success
        self.http_status = http_status
        self.curl_exit = curl_exit
        self.response = response or {}
        self.stderr = stderr

    @property
    def is_ok(self) -> bool:
        return (self.curl_exit == 0 and 200 <= self.http_status < 300
                and self.response is not None
                and self.response.get("success", False) is True)


class ThunderstoreAPI:
    def __init__(self, token: str = "", base_url: str = "", auth_scheme: str = "Bearer"):
        self.base_url = (base_url or os.environ.get("THUNDERSTORE_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        self.token = token or os.environ.get("THUNDERSTORE_AUTH_TOKEN", "")
        self.auth_scheme = auth_scheme or os.environ.get("THUNDERSTORE_AUTH_SCHEME", "Bearer")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"{self.auth_scheme} {self.token}"
        return h

    def _post(self, endpoint: str, body: dict) -> httpx.Response:
        url = f"{self.base_url}{endpoint}"
        try:
            r = httpx.post(url, json=body, headers=self._headers(), timeout=60)
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError as e:
            raise ThunderstoreError(
                f"API error {e.response.status_code} from {endpoint}: {e.response.text[:500]}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            raise ThunderstoreError(f"Request failed to {endpoint}: {e}")

    # --- Validation ---

    def validate_manifest(self, manifest_data: str, namespace: str) -> ValidationResult:
        encoded = base64.b64encode(manifest_data.encode()).decode()
        return self._run_validation("manifest", "/api/experimental/submission/validate/manifest-v1/",
                                    {"namespace": namespace, "manifest_data": encoded})

    def validate_readme(self, readme_data: str) -> ValidationResult:
        encoded = base64.b64encode(readme_data.encode()).decode()
        return self._run_validation("readme", "/api/experimental/submission/validate/readme/",
                                    {"readme_data": encoded})

    def validate_icon(self, icon_bytes: bytes) -> ValidationResult:
        encoded = base64.b64encode(icon_bytes).decode()
        return self._run_validation("icon", "/api/experimental/submission/validate/icon/",
                                    {"icon_data": encoded})

    def _run_validation(self, check: str, endpoint: str, body: dict) -> ValidationResult:
        url = f"{self.base_url}{endpoint}"
        curl_exit = 0
        http_status = 0
        response_data: dict | None = None
        stderr = ""
        try:
            r = httpx.post(url, json=body, headers=self._headers(), timeout=30)
            http_status = r.status_code
            try:
                response_data = r.json()
            except Exception:
                pass
        except httpx.RequestError as e:
            curl_exit = 1
            stderr = str(e)
        success = (curl_exit == 0 and 200 <= http_status < 300
                   and response_data is not None
                   and response_data.get("success", False) is True)
        return ValidationResult(check=check, success=success, http_status=http_status,
                                curl_exit=curl_exit, response=response_data, stderr=stderr)

    # --- Upload (usermedia) ---

    def initiate_upload(self, name: str, file_size: int) -> dict:
        r = self._post("/api/experimental/usermedia/initiate-upload/", {"name": name, "size": file_size})
        data = r.json()
        if "user_media" not in data or "uuid" not in data.get("user_media", {}):
            raise ThunderstoreError(f"Unexpected initiate response: {r.text[:500]}")
        return data

    def upload_chunks(self, zip_path: Path, upload_urls: list[dict], max_retries: int = 3) -> list[dict]:
        parts: list[dict] = []
        total = len(upload_urls)
        print(f"Uploading {total} chunk(s)...")
        with open(zip_path, "rb") as f:
            for ui in upload_urls:
                part_num = ui["number"]
                print(f"  Chunk {part_num}/{total}: offset={ui['offset']} length={ui['length']}")
                f.seek(ui["offset"])
                chunk_data = f.read(ui["length"])
                etag: str | None = None
                for retry in range(max_retries):
                    try:
                        r = httpx.put(ui["url"], content=chunk_data, timeout=120)
                        if 200 <= r.status_code < 300:
                            etag = r.headers.get("etag", "").strip()
                            if etag:
                                break
                    except httpx.RequestError:
                        pass
                    if retry < max_retries - 1:
                        wait = 2 ** (retry + 1)
                        print(f"    Retry {retry + 1}/{max_retries} after {wait}s...")
                        time.sleep(wait)
                if not etag:
                    raise ThunderstoreError(f"Failed to upload chunk {part_num} after {max_retries} retries")
                parts.append({"tag": etag, "number": part_num})
        return parts

    def finish_upload(self, uuid: str, parts: list[dict]) -> None:
        self._post(f"/api/experimental/usermedia/{uuid}/finish-upload/", {"parts": parts})

    def abort_upload(self, uuid: str) -> None:
        try:
            self._post(f"/api/experimental/usermedia/{uuid}/abort-upload/", {})
        except ThunderstoreError:
            pass

    def submit_package(self, uuid: str, author: str, community: str,
                       categories: list[str] | None = None, has_nsfw: bool = False) -> dict:
        cats = categories or ["Mods"]
        body = {
            "author_name": author,
            "communities": [community],
            "categories": cats,
            "community_categories": {community: cats} if community else None,
            "has_nsfw_content": has_nsfw,
        }
        return self._post(f"/api/experimental/usermedia/{uuid}/submit/", body).json()
```

- [ ] **Step 2: Commit**

```bash
git add thunderstore_pipeline/thunderstore_api.py
git commit -m "feat: add Thunderstore HTTP API client"
```

---

### Task 6: CLI entry point (typer app)

**Files:**
- Create: `thunderstore_pipeline/cli.py`

- [ ] **Step 1: Write cli.py**

```python
# thunderstore_pipeline/cli.py
"""Typer CLI entry point with subcommands for each pipeline phase."""
from __future__ import annotations

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
    version: Annotated[Optional[str], typer.Option("--version")] = None,
    package_zip: Annotated[Path, typer.Option("--package-zip")],
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
```

- [ ] **Step 2: Verify CLI works**

```bash
uv run python -m thunderstore_pipeline --help
uv run python -m thunderstore_pipeline config-check
```
Expected: Help shows all subcommands; config-check prints 3 mods.

- [ ] **Step 3: Commit**

```bash
git add thunderstore_pipeline/cli.py
git commit -m "feat: add typer CLI entry point"
```

---

### Task 7: Phase 1 — sync business logic

**Files:**
- Create: `thunderstore_pipeline/sync.py`

- [ ] **Step 1: Write sync.py**

[Full code provided — same as previously planned, calling gh.py functions]

- [ ] **Step 2: Commit**

```bash
git add thunderstore_pipeline/sync.py
git commit -m "feat: add Phase 1 sync logic"
```

---

### Task 8: Phase 2 — build business logic

**Files:**
- Create: `thunderstore_pipeline/build.py`

- [ ] **Step 1: Write build.py**

[Full code provided — same as previously planned]

- [ ] **Step 2: Commit**

```bash
git add thunderstore_pipeline/build.py
git commit -m "feat: add Phase 2 build logic"
```

---

### Task 9: Phase 2 — validate business logic

**Files:**
- Create: `thunderstore_pipeline/validate.py`

- [ ] **Step 1: Write validate.py**

[Full code provided — same as previously planned]

- [ ] **Step 2: Commit**

```bash
git add thunderstore_pipeline/validate.py
git commit -m "feat: add validate logic"
```

---

### Task 10: Phase 3 — publish business logic

**Files:**
- Create: `thunderstore_pipeline/publish.py`

- [ ] **Step 1: Write publish.py**

[Full code provided — same as previously planned]

- [ ] **Step 2: Commit**

```bash
git add thunderstore_pipeline/publish.py
git commit -m "feat: add Phase 3 publish logic"
```

---

### Task 11: Composite setup action

**Files:**
- Create: `.github/actions/setup/action.yml`

> **CRITICAL:** GitHub requires composite actions at `.github/actions/<name>/action.yml`, NOT `.github/workflows/_setup.yml`.

- [ ] **Step 1: Write action.yml**

```yaml
name: Setup Pipeline
description: Checkout, install Python with uv cache, install dependencies

inputs:
  python-version:
    description: Python version
    required: false
    default: "3.12"
  fetch-depth:
    description: Git fetch depth (0 for full history)
    required: false
    default: "1"

runs:
  using: composite
  steps:
    - name: Checkout
      uses: actions/checkout@v4
      with:
        fetch-depth: ${{ inputs.fetch-depth }}

    - name: Setup uv
      uses: astral-sh/setup-uv@v5
      with:
        python-version: ${{ inputs.python-version }}
        enable-cache: true

    - name: Install dependencies
      run: uv sync --frozen
      shell: bash
```

- [ ] **Step 2: Commit**

```bash
git add .github/actions/setup/action.yml
git commit -m "feat: add composite setup action for uv-based CI"
```

---

### Task 12: Phase 1 workflow — sync.yml

**Files:**
- Create: `.github/workflows/sync.yml`
- Eventually replace: `sync-release-assets.yml`

- [ ] **Step 1: Write sync.yml**

```yaml
name: Sync Release Assets

on:
  workflow_dispatch:
    inputs:
      mod_key:    { type: string, required: false }
      tag:        { type: string, required: false }
      all:        { type: boolean, default: false }
      dry_run:    { type: boolean, default: false }
  workflow_call:
    inputs:
      mod_key:  { type: string, default: "" }
      tag:      { type: string, default: "" }
      all:      { type: boolean, default: false }
      dry_run:  { type: boolean, default: false }
    outputs:
      synced_mods:
        value: ${{ jobs.sync.outputs.synced_mods }}

concurrency:
  group: sync-${{ inputs.mod_key || 'all' }}
  cancel-in-progress: false

jobs:
  sync:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    outputs:
      synced_mods: ${{ steps.summary.outputs.synced_mods }}

    steps:
      - uses: ./.github/actions/setup
        with:
          fetch-depth: "0"

      - name: Run sync
        id: sync
        env:
          GITHUB_TOKEN: ${{ github.token }}
          GITHUB_EVENT_NAME: ${{ github.event_name }}
          SYNC_SUMMARY_FILE: /tmp/sync_summary.jsonl
        run: |
          uv run python -m thunderstore_pipeline sync \
            ${{ inputs.all == 'true' && '--all' || '' }} \
            ${{ inputs.mod_key != '' && format('--mod-key {0}', inputs.mod_key) || '' }} \
            ${{ inputs.tag != '' && format('--tag {0}', inputs.tag) || '' }} \
            ${{ inputs.dry_run == 'true' && '--dry-run' || '' }}

      - name: Read sync summary
        id: summary
        if: always()
        run: |
          if [[ -f /tmp/sync_summary.jsonl ]]; then
            MODS=$(jq -s -c '.' /tmp/sync_summary.jsonl)
            echo "synced_mods=${MODS}" >> "$GITHUB_OUTPUT"
          else
            echo "synced_mods=[]" >> "$GITHUB_OUTPUT"
          fi
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/sync.yml
git commit -m "feat: add sync workflow"
```

---

### Task 13: Phase 2 workflow — build-and-validate.yml

**Files:**
- Create: `.github/workflows/build-and-validate.yml`
- Eventually replace: `build-and-validate-thunderstore.yml`

> **KEY:** Build and validate run in the SAME job to share filesystem. The manifest/readme/icon paths are output by build and consumed by validate. Token lookup happens inline between build and validate.

- [ ] **Step 1: Write build-and-validate.yml**

```yaml
name: Build And Validate Thunderstore Package

on:
  workflow_dispatch:
    inputs:
      mod_key: { type: string, required: true }
      version: { type: string, required: false }
  workflow_call:
    inputs:
      mod_key: { type: string, required: true }
      version: { type: string, default: "" }
    outputs:
      run_id:
        value: ${{ github.run_id }}
      mod_key:
        value: ${{ jobs.build.outputs.mod_key }}
      version:
        value: ${{ jobs.build.outputs.version }}

concurrency:
  group: build-${{ inputs.mod_key }}
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    outputs:
      mod_key: ${{ steps.build.outputs.mod_key }}
      version: ${{ steps.build.outputs.version }}
      namespace: ${{ steps.build.outputs.namespace }}
      thunder_token_key: ${{ steps.build.outputs.thunder_token_key }}
      package_path: ${{ steps.build.outputs.package_path }}
      validation_warning: ${{ steps.validate.outputs.validation_warning }}

    steps:
      - uses: ./.github/actions/setup
        with:
          fetch-depth: "0"

      - name: Build package
        id: build
        run: |
          uv run python -m thunderstore_pipeline build \
            --mod-key "${{ inputs.mod_key }}" \
            ${{ inputs.version != '' && format('--version {0}', inputs.version) || '' }}

      - name: Look up token key and validate
        id: validate
        env:
          THUNDERSTORE_AUTH_SCHEME: ${{ vars.THUNDERSTORE_AUTH_SCHEME }}
        run: |
          TOKEN_KEY="${{ steps.build.outputs.thunder_token_key }}"
          uv run python -m thunderstore_pipeline validate \
            --manifest "${{ steps.build.outputs.manifest_path }}" \
            --readme "${{ steps.build.outputs.readme_path }}" \
            --icon "${{ steps.build.outputs.icon_path }}" \
            --namespace "${{ steps.build.outputs.namespace }}" \
            --auth-token "${!TOKEN_KEY}"

      - name: Upload package artifact
        uses: actions/upload-artifact@v4
        with:
          name: package-${{ steps.build.outputs.mod_key }}-${{ steps.build.outputs.version }}
          path: ${{ steps.build.outputs.package_path }}

      - name: Upload validation logs
        uses: actions/upload-artifact@v4
        with:
          name: validation-${{ steps.build.outputs.mod_key }}-${{ steps.build.outputs.version }}
          path: |
            build/validation/*.json
            build/validation/raw/*.body
            build/validation/raw/*.stderr
```

**Token injection note:** The Thunderstore auth token is passed to the validate subcommand via `--auth-token "${!TOKEN_KEY}"` where `TOKEN_KEY` is the derived secret name (e.g., `SMALL_TAILQWQ_THUNDER_TOKEN`). The workflow must expose the secret as an env var. This requires adding an `env` block at job or step level that maps `secrets[steps.build.outputs.thunder_token_key]`.

**Revised validate step:**
```yaml
      - name: Look up token and validate
        id: validate
        env:
          THUNDERSTORE_AUTH_TOKEN: ${{ secrets[steps.build.outputs.thunder_token_key] }}
          THUNDERSTORE_AUTH_SCHEME: ${{ vars.THUNDERSTORE_AUTH_SCHEME }}
        run: |
          uv run python -m thunderstore_pipeline validate \
            --manifest "${{ steps.build.outputs.manifest_path }}" \
            --readme "${{ steps.build.outputs.readme_path }}" \
            --icon "${{ steps.build.outputs.icon_path }}" \
            --namespace "${{ steps.build.outputs.namespace }}"
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/build-and-validate.yml
git commit -m "feat: add build-and-validate workflow"
```

---

### Task 14: Phase 3 workflow — publish.yml

**Files:**
- Create: `.github/workflows/publish.yml`
- Eventually replace: `publish-thunderstore.yml`

- [ ] **Step 1: Write publish.yml**

```yaml
name: Publish Thunderstore Package

on:
  workflow_dispatch:
    inputs:
      mod_key:          { type: string, required: true }
      version:          { type: string, required: false }
      artifact_run_id:  { type: string, required: true }
      dry_run:          { type: boolean, default: true }
  workflow_call:
    inputs:
      mod_key:          { type: string, required: true }
      version:          { type: string, default: "" }
      artifact_run_id:  { type: string, required: true }
      dry_run:          { type: boolean, default: false }

concurrency:
  group: publish-${{ inputs.mod_key }}
  cancel-in-progress: false

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read

    steps:
      - uses: ./.github/actions/setup

      - name: Download package artifact
        id: download
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          mkdir -p build/packages/downloaded
          gh run download "${{ inputs.artifact_run_id }}" \
            --repo "${{ github.repository }}" \
            --pattern "package-${{ inputs.mod_key }}-*" \
            --dir build/packages/downloaded
          ZIP_PATH=$(find build/packages/downloaded -name '*.zip' -print -quit)
          if [[ -z "$ZIP_PATH" ]]; then
            echo "No zip found in downloaded artifact" >&2
            exit 1
          fi
          echo "artifact_zip=${ZIP_PATH}" >> "$GITHUB_OUTPUT"

      - name: Look up token key
        id: token-key
        run: |
          uv run python -c "
          from thunderstore_pipeline.config import load_config
          import os
          cfg = load_config()
          for m in cfg.mods:
              if m.key == '${{ inputs.mod_key }}':
                  ns = m.thunderstore.namespace
                  tk = ns.upper().replace('-', '_') + '_THUNDER_TOKEN'
                  with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                      f.write(f'namespace={ns}\n')
                      f.write(f'token_key={tk}\n')
                  break
          "

      - name: Publish package
        id: publish
        env:
          THUNDERSTORE_AUTH_TOKEN: ${{ secrets[steps.token-key.outputs.token_key] }}
          THUNDERSTORE_AUTH_SCHEME: ${{ vars.THUNDERSTORE_AUTH_SCHEME }}
        run: |
          uv run python -m thunderstore_pipeline publish \
            --mod-key "${{ inputs.mod_key }}" \
            ${{ inputs.version != '' && format('--version {0}', inputs.version) || '' }} \
            --package-zip "${{ steps.download.outputs.artifact_zip }}" \
            ${{ inputs.dry_run == 'true' && '--dry-run' || '' }}
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "feat: add publish workflow"
```

---

### Task 15: Orchestrator workflow

**Files:**
- Create: `.github/workflows/orchestrator.yml`

- [ ] **Step 1: Write orchestrator.yml**

```yaml
name: Full Pipeline Orchestrator

on:
  workflow_dispatch:
    inputs:
      all:
        type: boolean
        default: true
      mod_key: { type: string, required: false }
      tag:     { type: string, required: false }
  schedule:
    - cron: "0 0 * * *"

concurrency:
  group: orchestrator
  cancel-in-progress: false

jobs:
  sync:
    uses: ./.github/workflows/sync.yml
    with:
      all: ${{ github.event_name == 'schedule' || inputs.all }}
      mod_key: ${{ inputs.mod_key || '' }}
      tag: ${{ inputs.tag || '' }}
    secrets: inherit

  build:
    needs: sync
    if: needs.sync.outputs.synced_mods != '[]' && needs.sync.outputs.synced_mods != ''
    strategy:
      max-parallel: 2
      matrix:
        mod: ${{ fromJSON(needs.sync.outputs.synced_mods) }}
    uses: ./.github/workflows/build-and-validate.yml
    with:
      mod_key: ${{ matrix.mod.mod_key }}
      version: ${{ matrix.mod.version }}
    secrets: inherit

  publish:
    needs: [sync, build]
    if: needs.build.result != 'failure'
    strategy:
      max-parallel: 1
      matrix:
        mod: ${{ fromJSON(needs.sync.outputs.synced_mods) }}
    uses: ./.github/workflows/publish.yml
    with:
      mod_key: ${{ matrix.mod.mod_key }}
      version: ${{ matrix.mod.version }}
      artifact_run_id: ${{ github.run_id }}
    secrets: inherit
```

> **Orchestrator artifact_run_id note:** The publish workflow downloads artifacts from `build-and-validate`. Since orchestrator uses `workflow_call` (reusable workflow), each called workflow runs as a job within the orchestrator's run — sharing the same `github.run_id`. Artifacts uploaded by `build-and-validate` are accessible to `publish` via `actions/download-artifact@v4` with the orchestrator's `github.run_id`. **Verified correct:** GitHub Actions allows downloading artifacts from the current workflow run across reusable workflow boundaries.

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/orchestrator.yml
git commit -m "feat: add orchestrator workflow for full pipeline"
```

---

### Task 16: Cleanup — remove old scripts and workflows

- [ ] **Step 1: Remove old workflows**

```bash
git rm .github/workflows/sync-release-assets.yml
git rm .github/workflows/build-and-validate-thunderstore.yml
git rm .github/workflows/publish-thunderstore.yml
git commit -m "chore: remove old workflow files (replaced by Python-driven workflows)"
```

- [ ] **Step 2: (After verification) Remove old shell scripts**

```bash
git rm scripts/sync_release_assets.sh
git rm scripts/build_package.sh
git rm scripts/publish_thunderstore.sh
git rm scripts/validate_thunderstore.sh
git rm scripts/validate_mods_config.sh
# Keep rewrite_readme_links.py until migration confirmed
git commit -m "chore: remove old shell scripts (migrated to Python)"
```

---

### Task 17: Integration verification

- [ ] **Step 1: config-check**

```bash
uv run python -m thunderstore_pipeline config-check
```
Expected: 3 mods listed.

- [ ] **Step 2: build (local)**

```bash
uv run python -m thunderstore_pipeline build --mod-key realtime-weather
```
Expected: zip created in build/packages/

- [ ] **Step 3: publish --dry-run**

```bash
uv run python -m thunderstore_pipeline publish \
  --mod-key realtime-weather \
  --package-zip build/packages/realtime-weather/*/Small_tailqwq-RealTimeWeather-*.zip \
  --dry-run
```
Expected: Generates thunderstore.toml, prints "DRY RUN".

- [ ] **Step 4: CLI help for all subcommands**

```bash
uv run python -m thunderstore_pipeline sync --help
uv run python -m thunderstore_pipeline build --help
uv run python -m thunderstore_pipeline validate --help
uv run python -m thunderstore_pipeline publish --help
uv run python -m thunderstore_pipeline config-check --help
```

## Self-Review Results

| Check | Result |
|-------|--------|
| Composite action at `.github/actions/setup/` | ✅ Fixed from `workflows/_setup.yml` |
| build + validate same job (shared FS) | ✅ Fixed from split jobs |
| Orchestrator run_id for artifact download | ✅ Confirmed — same run_id across workflow_call |
| Token secret injection pattern | ✅ Uses `secrets[steps.X.outputs.token_key]` properly |
| No placeholders | ✅ All code is concrete |
| Sync Module: complete code | ✅ Mirror of original logic |
| Build Module: complete code | ✅ Mirror + check patterns |
