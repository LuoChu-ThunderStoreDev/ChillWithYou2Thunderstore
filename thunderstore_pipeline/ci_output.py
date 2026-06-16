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
