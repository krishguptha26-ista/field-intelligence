from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# This module sorts before the API regression module during discovery. Pin the
# same isolated process configuration before importing any server module so a
# test run can never inherit a developer's live provider or deployment secrets.
_TEST_DB = Path(__file__).resolve().parent.parent / "var" / "test_regressions.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["LLM_PROVIDER"] = "fixture"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_MAPS_API_KEY"] = ""
os.environ["APP_ENV"] = "testing"
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""

from server import config
from server.blob_store import (BlobStoreUnavailable, delete_blob, get_blob,
                               ensure_remote_bucket, put_blob)


class PersistentBlobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.digest = hashlib.sha256(b"private evidence").hexdigest()
        self.config_patches = [
            patch.object(config, "REMOTE_STORAGE_CONFIGURED", True),
            patch.object(config, "SUPABASE_URL", "https://project.supabase.co"),
            patch.object(config, "SUPABASE_SERVICE_ROLE_KEY", "test-service-role"),
            patch.object(config, "SUPABASE_STORAGE_BUCKET", "evidence"),
        ]
        for item in self.config_patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.config_patches):
            item.stop()

    @patch("server.blob_store.httpx.post")
    def test_remote_upload_is_private_digest_addressed_and_upserted(self, post) -> None:
        post.return_value.raise_for_status = MagicMock()
        put_blob(self.digest, b"private evidence", "image/png")
        _, kwargs = post.call_args
        self.assertTrue(post.call_args.args[0].endswith(f"/evidence/{self.digest}"))
        self.assertEqual(kwargs["content"], b"private evidence")
        self.assertEqual(kwargs["headers"]["x-upsert"], "true")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-service-role")

    @patch("server.blob_store.httpx.get")
    def test_remote_download_preserves_bytes_and_mime(self, get) -> None:
        get.return_value.status_code = 200
        get.return_value.content = b"private evidence"
        get.return_value.headers = {"content-type": "image/png; charset=binary"}
        get.return_value.raise_for_status = MagicMock()
        blob = get_blob(self.digest)
        self.assertIsNotNone(blob)
        self.assertEqual(blob.content, b"private evidence")
        self.assertEqual(blob.mime_type, "image/png")

    @patch("server.blob_store.httpx.get")
    def test_supabase_missing_object_400_maps_to_not_found(self, get) -> None:
        get.return_value.status_code = 400
        get.return_value.json.return_value = {"code": "NoSuchKey"}
        self.assertIsNone(get_blob(self.digest))

    @patch("server.blob_store.httpx.request")
    def test_remote_delete_targets_only_the_exact_digest(self, request) -> None:
        request.return_value.raise_for_status = MagicMock()
        self.assertTrue(delete_blob(self.digest))
        self.assertEqual(request.call_args.kwargs["json"], {"prefixes": [self.digest]})

    @patch("server.blob_store.httpx.post")
    def test_provider_error_is_redacted(self, post) -> None:
        import httpx
        post.side_effect = httpx.ConnectError("secret-bearing upstream failure")
        with self.assertRaisesRegex(BlobStoreUnavailable,
                                    "persistent evidence storage unavailable"):
            put_blob(self.digest, b"private evidence", "image/png")

    @patch("server.blob_store.httpx.Client")
    def test_supabase_missing_bucket_400_is_created_privately(self, client_cls) -> None:
        client = client_cls.return_value.__enter__.return_value
        missing = MagicMock(status_code=400)
        missing.json.return_value = {"code": "NoSuchBucket"}
        created = MagicMock(status_code=200)
        created.raise_for_status = MagicMock()
        client.get.return_value = missing
        client.post.return_value = created
        ensure_remote_bucket()
        payload = client.post.call_args.kwargs["json"]
        self.assertFalse(payload["public"])
        self.assertEqual(payload["id"], "evidence")


if __name__ == "__main__":
    unittest.main()
