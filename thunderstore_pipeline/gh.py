"""GitHub CLI (gh) wrapper for release, branch, and dispatch operations."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .models import ReleaseMeta, ReleaseAsset


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
        assets=[ReleaseAsset(name=a["name"], browser_download_url=a["browser_download_url"])
                for a in data.get("assets", [])],
    )


def download_asset(url: str, dest: Path) -> Path:
    _run(["curl", "-fsSL", url, "-o", str(dest)], timeout=300)
    return dest


def list_all_releases(owner: str, repo: str) -> list[str]:
    """Return all release tag names, newest first (paginated)."""
    result = _run(["gh", "api", f"repos/{owner}/{repo}/releases",
                   "--jq", ".[].tag_name", "--paginate"])
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]


def get_raw_file(owner: str, repo: str, ref: str, path: str) -> str | None:
    """Fetch raw file from raw.githubusercontent.com. Returns None on 404."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    result = subprocess.run(["curl", "-fsSL", url], capture_output=True, text=True, timeout=30)
    return result.stdout if result.returncode == 0 else None


def dispatch_workflow(event_type: str, payload: dict) -> None:
    """Dispatch a repository_dispatch event."""
    import os
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        r = _run(["gh", "repo", "view", "--json", "nameWithOwner"])
        repo = json.loads(r.stdout)["nameWithOwner"]
    body = json.dumps({"event_type": event_type, "client_payload": payload})
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/dispatches", "--method", "POST", "--input", "-"],
        input=body, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise GhError(f"Dispatch failed: {result.stderr}", stderr=result.stderr)


def remote_branch_exists(branch: str) -> bool:
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        capture_output=True, timeout=30,
    )
    return result.returncode == 0


def list_versions_on_branch(branch: str) -> list[str]:
    """List semver versions at branch root (direct <version>/ dirs)."""
    try:
        result = _run(["git", "ls-tree", "--name-only", f"origin/{branch}"])
    except GhError as e:
        print(f"Warning: failed to list versions on {branch}: {e}", file=sys.stderr)
        return []
    versions = set()
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("/")
        if re.match(r"^\d+\.\d+\.\d+$", parts[0]):
            versions.add(parts[0])
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
    commit_msg: str, dry_run: bool = False, force: bool = False,
) -> None:
    """Push files to a git branch via worktree. Skips if version exists unless force=True."""
    if dry_run:
        print(f"[DRY RUN] skip pushing {branch}:{version}")
        return

    target_rel = version
    worktree_dir = Path(tempfile.mkdtemp())

    try:
        if remote_branch_exists(branch):
            _run(["git", "fetch", "origin", branch])
            _run(["git", "worktree", "add", str(worktree_dir), f"origin/{branch}"])
            subprocess.run(["git", "checkout", "-B", branch],
                           cwd=worktree_dir, capture_output=True, timeout=30)
        else:
            base = os.environ.get("GITHUB_SHA", "HEAD")
            _run(["git", "worktree", "add", "--detach", str(worktree_dir), base])
            subprocess.run(["git", "checkout", "--orphan", branch],
                           cwd=worktree_dir, capture_output=True, timeout=30)
            subprocess.run(["git", "rm", "-rf", "."],
                           cwd=worktree_dir, capture_output=True, timeout=30)

        target_dir = worktree_dir / target_rel
        if not force and target_dir.exists() and any(target_dir.iterdir()):
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

        subprocess.run(["git", "add", target_rel],
                       cwd=worktree_dir, capture_output=True, timeout=30)
        diff_rc = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=worktree_dir, capture_output=True, timeout=30,
        ).returncode
        if diff_rc == 0:
            print(f"No staged changes for {mod_key}@{version}, skip commit")
            return

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"],
                       cwd=worktree_dir, capture_output=True, timeout=30)
        subprocess.run(["git", "config", "user.email",
                        "41898282+github-actions[bot]@users.noreply.github.com"],
                       cwd=worktree_dir, capture_output=True, timeout=30)
        subprocess.run(["git", "commit", "-m", commit_msg],
                       cwd=worktree_dir, capture_output=True, timeout=30)
        _run(["git", "push", "origin", branch])
        print(f"Pushed {mod_key}@{version} to {branch}")
    finally:
        subprocess.run(["git", "worktree", "remove", str(worktree_dir), "--force"],
                       capture_output=True, timeout=30)
        shutil.rmtree(worktree_dir, ignore_errors=True)
