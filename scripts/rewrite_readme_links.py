#!/usr/bin/env python3
import argparse
import posixpath
import re
from pathlib import PurePosixPath

INLINE_LINK_RE = re.compile(r"(!?\[[^\]]*\]\()([^\)]+)(\))")
REF_LINK_RE = re.compile(r"^(\s*\[[^\]]+\]:\s*)(\S+)(.*)$")


def normalize_rel_path(readme_path: str, target: str) -> str:
    base_dir = str(PurePosixPath(readme_path).parent)
    if target.startswith("/"):
        combined = target.lstrip("/")
    else:
        combined = posixpath.normpath(posixpath.join(base_dir, target))
    return combined.lstrip("./")


def is_relative_url(url: str) -> bool:
    lowered = url.lower()
    prefixes = ("http://", "https://", "mailto:", "data:", "#")
    return not lowered.startswith(prefixes)


def split_anchor(url: str):
    if "#" in url:
        p, a = url.split("#", 1)
        return p, "#" + a
    return url, ""


def rewrite_url(
    url: str, owner: str, repo: str, ref: str, readme_path: str, is_image: bool
) -> str:
    if not is_relative_url(url):
        return url
    path_part, anchor = split_anchor(url)
    normalized = normalize_rel_path(readme_path, path_part)
    if is_image:
        base = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}"
        return f"{base}/{normalized}{anchor}"
    base = f"https://github.com/{owner}/{repo}/blob/{ref}"
    return f"{base}/{normalized}{anchor}"


def rewrite_inline(text: str, owner: str, repo: str, ref: str, readme_path: str) -> str:
    def repl(match: re.Match) -> str:
        prefix, url, suffix = match.groups()
        is_image = prefix.startswith("!")

        parts = url.split(maxsplit=1)
        path_url = parts[0]
        rest = "" if len(parts) == 1 else " " + parts[1]
        new_url = rewrite_url(path_url, owner, repo, ref, readme_path, is_image)
        return f"{prefix}{new_url}{rest}{suffix}"

    return INLINE_LINK_RE.sub(repl, text)


def rewrite_ref_lines(
    text: str, owner: str, repo: str, ref: str, readme_path: str
) -> str:
    out_lines = []
    for line in text.splitlines():
        m = REF_LINK_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        left, url, rest = m.groups()
        new_url = rewrite_url(url, owner, repo, ref, readme_path, False)
        out_lines.append(f"{left}{new_url}{rest}")
    return "\n".join(out_lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--readme-path", required=True)
    args = parser.parse_args()

    text = open(args.input, "r", encoding="utf-8").read()
    text = rewrite_inline(text, args.owner, args.repo, args.ref, args.readme_path)
    text = rewrite_ref_lines(text, args.owner, args.repo, args.ref, args.readme_path)

    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


if __name__ == "__main__":
    main()
