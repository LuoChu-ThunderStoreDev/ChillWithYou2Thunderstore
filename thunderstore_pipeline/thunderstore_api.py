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
    def __init__(
        self,
        check: str,
        success: bool,
        http_status: int = 0,
        curl_exit: int = 0,
        response: dict | None = None,
        stderr: str = "",
    ):
        self.check = check
        self.success = success
        self.http_status = http_status
        self.curl_exit = curl_exit
        self.response = response or {}
        self.stderr = stderr

    @property
    def is_ok(self) -> bool:
        return (
            self.curl_exit == 0
            and 200 <= self.http_status < 300
            and self.response is not None
            and self.response.get("success", False) is True
        )


class ThunderstoreAPI:
    def __init__(
        self,
        token: str = "",
        base_url: str = "",
        auth_scheme: str = "Bearer",
    ):
        self.base_url = (
            base_url
            or os.environ.get("THUNDERSTORE_API_BASE", DEFAULT_API_BASE)
        ).rstrip("/")
        self.token = token or os.environ.get("THUNDERSTORE_AUTH_TOKEN", "")
        self.auth_scheme = auth_scheme or os.environ.get(
            "THUNDERSTORE_AUTH_SCHEME", "Bearer"
        )

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

    def validate_manifest(
        self, manifest_data: str, namespace: str
    ) -> ValidationResult:
        encoded = base64.b64encode(manifest_data.encode()).decode()
        return self._run_validation(
            "manifest",
            "/api/experimental/submission/validate/manifest-v1/",
            {"namespace": namespace, "manifest_data": encoded},
        )

    def validate_readme(self, readme_data: str) -> ValidationResult:
        encoded = base64.b64encode(readme_data.encode()).decode()
        return self._run_validation(
            "readme",
            "/api/experimental/submission/validate/readme/",
            {"readme_data": encoded},
        )

    def validate_icon(self, icon_bytes: bytes) -> ValidationResult:
        encoded = base64.b64encode(icon_bytes).decode()
        return self._run_validation(
            "icon",
            "/api/experimental/submission/validate/icon/",
            {"icon_data": encoded},
        )

    def _run_validation(
        self, check: str, endpoint: str, body: dict
    ) -> ValidationResult:
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
        success = (
            curl_exit == 0
            and 200 <= http_status < 300
            and response_data is not None
            and response_data.get("success", False) is True
        )
        return ValidationResult(
            check=check,
            success=success,
            http_status=http_status,
            curl_exit=curl_exit,
            response=response_data,
            stderr=stderr,
        )

    # --- Upload (usermedia) ---

    def check_package_exists(self, namespace: str, name: str) -> dict | None:
        """Check if a package exists on Thunderstore.

        GET /api/experimental/package/{namespace}/{name}/
        Returns parsed PackageExperimental dict if exists (200), None if not (404/error).
        No auth required.
        """
        url = f"{self.base_url}/api/experimental/package/{namespace}/{name}/"
        try:
            r = httpx.get(url, headers={"Content-Type": "application/json"}, timeout=30)
            if r.status_code == 200:
                return r.json()
            return None
        except httpx.RequestError:
            return None

    def initiate_upload(self, name: str, file_size: int) -> dict:
        r = self._post(
            "/api/experimental/usermedia/initiate-upload/",
            {"filename": name, "file_size_bytes": file_size},
        )
        data = r.json()
        if "user_media" not in data or "uuid" not in data.get("user_media", {}):
            raise ThunderstoreError(
                f"Unexpected initiate response: {r.text[:500]}"
            )
        return data

    def _upload_single_chunk(
        self, url: str, chunk_data: bytes, max_retries: int
    ) -> str:
        """Upload a single chunk with retries. Returns the ETag on success."""
        for retry in range(max_retries):
            try:
                r = httpx.put(url, content=chunk_data, timeout=120)
                if 200 <= r.status_code < 300:
                    etag = r.headers.get("etag", "").strip()
                    if etag:
                        return etag
            except httpx.RequestError:
                pass
            if retry < max_retries - 1:
                wait = 2 ** (retry + 1)
                print(f"    Retry {retry + 1}/{max_retries} after {wait}s...")
                time.sleep(wait)
        raise ThunderstoreError(
            f"Failed to upload chunk after {max_retries} retries"
        )

    def upload_chunks(
        self, zip_path: Path, upload_urls: list[dict], max_retries: int = 3
    ) -> list[dict]:
        parts: list[dict] = []
        total = len(upload_urls)
        print(f"Uploading {total} chunk(s)...")
        # Debug: show the first upload_url's keys so we can detect API field changes
        if upload_urls:
            print(f"  (upload_url keys: {list(upload_urls[0].keys())})")
        with open(zip_path, "rb") as f:
            for ui in upload_urls:
                part_num = ui.get("part_number", ui.get("number"))
                offset_val = ui.get("offset", ui.get("part_offset"))
                length_val = ui.get("length", ui.get("part_length"))
                print(
                    f"  Chunk {part_num}/{total}: offset={offset_val} length={length_val}"
                )
                f.seek(offset_val)
                chunk_data = f.read(length_val)
                etag = self._upload_single_chunk(ui["url"], chunk_data, max_retries)
                parts.append({"ETag": etag, "PartNumber": part_num})
        return parts

    def finish_upload(self, uuid: str, parts: list[dict]) -> None:
        self._post(
            f"/api/experimental/usermedia/{uuid}/finish-upload/",
            {"parts": parts},
        )

    def abort_upload(self, uuid: str) -> None:
        try:
            self._post(
                f"/api/experimental/usermedia/{uuid}/abort-upload/", {}
            )
        except ThunderstoreError:
            pass

    def submit_package(
        self,
        uuid: str,
        author: str,
        community: str,
        categories: list[str] | None = None,
        has_nsfw: bool = False,
    ) -> dict:
        cats = categories or ["mods"]
        body = {
            "upload_uuid": uuid,
            "author_name": author,
            "communities": [community],
            "categories": cats,
            "community_categories": {community: cats} if community else None,
            "has_nsfw_content": has_nsfw,
        }
        return self._post(
            "/api/experimental/submission/submit/", body
        ).json()
