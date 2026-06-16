"""Rewrite relative links in Markdown README to absolute GitHub URLs."""
from __future__ import annotations

import argparse
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
    prefixes = ("http://", "https://", "mailto:", "data:", "#")
    return not lowered.startswith(prefixes)


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
        return f"{base}/{normalized}{anchor}"
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


def rewrite_links(markdown: str, owner: str, repo: str,
                  ref: str, readme_path: str = "README.md") -> str:
    """Rewrite relative links in markdown to absolute GitHub URLs."""
    text = _rewrite_inline(markdown, owner, repo, ref, readme_path)
    text = _rewrite_ref_lines(text, owner, repo, ref, readme_path)
    return text


def rewrite_readme_file(input_path: str, output_path: str,
                        owner: str, repo: str, ref: str,
                        readme_path: str = "README.md") -> None:
    """Read a README file, rewrite links, write output."""
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()
    text = rewrite_links(text, owner, repo, ref, readme_path)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--readme-path", required=True)
    args = parser.parse_args()

    rewrite_readme_file(
        args.input, args.output,
        args.owner, args.repo, args.ref, args.readme_path,
    )


if __name__ == "__main__":
    main()
