"""Thunderstore package validation against the experimental validate API."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .thunderstore_api import ThunderstoreAPI
from .ci_output import CIOutput


def validate_package(
    manifest_path: Path,
    readme_path: Path,
    icon_path: Path,
    namespace: str,
    ci: CIOutput,
    auth_token: str | None = None,
    auth_scheme: str | None = None,
) -> None:
    for f, label in [(manifest_path, "manifest"), (readme_path, "readme"), (icon_path, "icon")]:
        if not f.exists():
            print(f"Missing validation file: {f} ({label})", file=sys.stderr)
            raise SystemExit(1)

    api = ThunderstoreAPI(token=auth_token or "", auth_scheme=auth_scheme or "Bearer")

    manifest_data = manifest_path.read_text(encoding="utf-8")
    readme_data = readme_path.read_text(encoding="utf-8")
    icon_data = icon_path.read_bytes()

    results = {
        "manifest": api.validate_manifest(manifest_data, namespace),
        "readme": api.validate_readme(readme_data),
        "icon": api.validate_icon(icon_data),
    }

    validation_dir = Path("build/validation")
    validation_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = validation_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    for check, result in results.items():
        result_path = validation_dir / f"{check}.json"
        result_path.write_text(json.dumps({
            "check": result.check,
            "http_status": result.http_status,
            "curl_exit": result.curl_exit,
            "response": result.response,
            "stderr": result.stderr,
        }, indent=2))
        body_path = raw_dir / f"{check}.body"
        body_path.write_text(json.dumps(result.response, indent=2))
        stderr_path = raw_dir / f"{check}.stderr"
        stderr_path.write_text(result.stderr)

    for check, result in results.items():
        print(f"{check} success: {result.is_ok}")
        print(f"  {check} status={result.http_status} curl_exit={result.curl_exit} success={result.is_ok}")

    ci.write_outputs(
        manifest_success=str(results["manifest"].is_ok).lower(),
        readme_success=str(results["readme"].is_ok).lower(),
        icon_success=str(results["icon"].is_ok).lower(),
    )

    all_ok = all(r.is_ok for r in results.values())
    if not all_ok:
        for f in raw_dir.glob("*.body"):
            body = f.read_text()
            if '"code": 1010' in body or '"code":1010' in body:
                print("WARNING: Detected upstream protection block (code 1010). "
                      "Validate from a different network/IP or CI runner.")
        for r in results.values():
            if r.http_status == 401:
                print("WARNING: Received HTTP 401 from Thunderstore API. "
                      "Check namespace token mapping and auth scheme.")
        ci.write_output("validation_warning", "true")
        print("WARNING: Validation checks reported issues. "
              "Detailed results are in build/validation/*.json")
        raise SystemExit(0)

    ci.write_output("validation_warning", "false")
    print("All Thunderstore validations passed")
