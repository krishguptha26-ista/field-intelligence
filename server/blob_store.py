"""Digest-addressed evidence storage with a private Supabase backend.

Development and tests retain the local ``var/uploads`` behaviour. Production
can use a private Supabase Storage bucket; the browser never receives the
service-role key or a direct object URL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from . import config


_DIGEST = re.compile(r"[a-f0-9]{64}")
_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/svg+xml": ".svg", "audio/wav": ".wav", "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3", "audio/aiff": ".aiff", "audio/aac": ".aac",
    "audio/ogg": ".ogg", "audio/flac": ".flac", "video/mp4": ".mp4",
    "video/webm": ".webm", "video/mpeg": ".mpeg", "video/quicktime": ".mov",
}


@dataclass(frozen=True)
class StoredBlob:
    content: bytes
    mime_type: str


class BlobStoreUnavailable(RuntimeError):
    """A safe public-facing error that never contains storage credentials."""


def _validate_digest(digest: str) -> None:
    if not _DIGEST.fullmatch(digest):
        raise ValueError("invalid evidence digest")


def _headers(*, mime_type: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
    }
    if mime_type:
        headers.update({"Content-Type": mime_type, "x-upsert": "true"})
    return headers


def _object_url(digest: str) -> str:
    return (f"{config.SUPABASE_URL}/storage/v1/object/"
            f"{config.SUPABASE_STORAGE_BUCKET}/{digest}")


def _supabase_error_code(response: httpx.Response) -> str:
    try:
        return str(response.json().get("code") or "")
    except ValueError:
        return ""


def ensure_remote_bucket() -> None:
    """Create the private bucket once; fail startup on a broken deployment."""
    if not config.REMOTE_STORAGE_CONFIGURED:
        return
    bucket_url = (f"{config.SUPABASE_URL}/storage/v1/bucket/"
                  f"{config.SUPABASE_STORAGE_BUCKET}")
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(bucket_url, headers=_headers())
            missing_bucket = response.status_code == 404
            if response.status_code == 400:
                missing_bucket = _supabase_error_code(response) == "NoSuchBucket"
            if missing_bucket:
                response = client.post(
                    f"{config.SUPABASE_URL}/storage/v1/bucket",
                    headers={**_headers(), "Content-Type": "application/json"},
                    json={"id": config.SUPABASE_STORAGE_BUCKET,
                          "name": config.SUPABASE_STORAGE_BUCKET,
                          "public": False,
                          "file_size_limit": 25 * 1024 * 1024},
                )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise BlobStoreUnavailable("persistent evidence storage unavailable") from exc


def put_blob(digest: str, content: bytes, mime_type: str) -> None:
    _validate_digest(digest)
    if config.REMOTE_STORAGE_CONFIGURED:
        try:
            response = httpx.post(
                _object_url(digest), content=content,
                headers=_headers(mime_type=mime_type), timeout=30,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BlobStoreUnavailable("persistent evidence storage unavailable") from exc
        return
    extension = _EXTENSIONS.get(mime_type, ".bin")
    path = config.UPLOADS_DIR / f"{digest}{extension}"
    if not path.exists():
        path.write_bytes(content)


def get_blob(digest: str) -> StoredBlob | None:
    _validate_digest(digest)
    if config.REMOTE_STORAGE_CONFIGURED:
        try:
            response = httpx.get(_object_url(digest), headers=_headers(), timeout=30)
            if (response.status_code == 404
                    or (response.status_code == 400
                        and _supabase_error_code(response) == "NoSuchKey")):
                return None
            response.raise_for_status()
            mime = response.headers.get("content-type", "application/octet-stream")
            return StoredBlob(response.content, mime.split(";", 1)[0])
        except httpx.HTTPError as exc:
            raise BlobStoreUnavailable("persistent evidence storage unavailable") from exc
    for path in config.UPLOADS_DIR.glob(f"{digest}.*"):
        if path.is_file():
            mime = next((kind for kind, suffix in _EXTENSIONS.items()
                         if suffix == path.suffix.lower()), "application/octet-stream")
            return StoredBlob(path.read_bytes(), mime)
    return None


def delete_blob(digest: str) -> bool:
    _validate_digest(digest)
    if config.REMOTE_STORAGE_CONFIGURED:
        try:
            response = httpx.request(
                "DELETE",
                f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_STORAGE_BUCKET}",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"prefixes": [digest]}, timeout=30,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BlobStoreUnavailable("persistent evidence storage unavailable") from exc
        return True
    removed = False
    for path in config.UPLOADS_DIR.glob(f"{digest}.*"):
        if path.is_file():
            path.unlink()
            removed = True
    return removed
