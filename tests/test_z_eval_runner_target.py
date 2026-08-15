from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Keep direct imports deterministic when this module is run alone.  The ``z``
# filename also makes full discovery load it after the broader regression suite,
# whose process-wide server configuration is established at import time.
os.environ["LLM_PROVIDER"] = "fixture"
os.environ["GEMINI_API_KEY"] = ""

from server.evals import runner


BUILD = runner.source_fingerprint()


HEALTH = {
    "ok": True,
    "active_provider": "fixture",
    "configured_provider": "fixture",
    "llm_provider": "fixture",
    "llm_model": "fixture",
    "reason": "deterministic fixture engine active",
    "build_fingerprint": BUILD,
}


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class _Client:
    def __init__(self, health: dict | None = None) -> None:
        self.health = HEALTH if health is None else health
        self.urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get(self, url: str) -> _Response:
        self.urls.append(url)
        return _Response(self.health)


class EvaluationTargetTests(unittest.TestCase):
    def test_api_url_is_explicit_and_rejects_ambiguous_targets(self) -> None:
        self.assertEqual(
            runner._normalise_api_url("http://127.0.0.1:8001"),
            "http://127.0.0.1:8001/api",
        )
        self.assertEqual(
            runner._normalise_api_url("https://example.test/base/api/"),
            "https://example.test/base/api",
        )
        for invalid in (
                "127.0.0.1:8001/api",
                "http://127.0.0.1:8001/not-api",
                "http://user:secret@127.0.0.1:8001/api",
                "http://127.0.0.1:8001/api?target=other",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(runner.EvaluationTargetError):
                    runner._normalise_api_url(invalid)

    def test_provider_expectation_fails_before_case_execution(self) -> None:
        client = _Client()
        with self.assertRaisesRegex(runner.EvaluationTargetError,
                                    "expected active provider 'gemini'"):
            runner._validate_system_under_test(
                client, "http://127.0.0.1:8001/api", "gemini", BUILD)
        self.assertEqual(client.urls, ["http://127.0.0.1:8001/api/health"])

    def test_health_contract_is_required(self) -> None:
        client = _Client({"ok": True, "active_provider": "fixture"})
        with self.assertRaisesRegex(runner.EvaluationTargetError,
                                    "missing required identity fields"):
            runner._validate_system_under_test(
                client, "http://127.0.0.1:8001/api")

    def test_stale_build_fails_before_case_execution(self) -> None:
        client = _Client()
        with self.assertRaisesRegex(runner.EvaluationTargetError,
                                    "stale or different server build"):
            runner._validate_system_under_test(
                client, "http://127.0.0.1:8001/api", "fixture", "deadbeef")

    def test_artifact_separates_sut_from_local_judge(self) -> None:
        client = _Client()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner.httpx, "Client", return_value=client), \
                patch.object(runner, "CASES", []), \
                patch.object(runner.config, "VAR_DIR", Path(directory)), \
                patch("server.gateway.provider_status", return_value={
                    "active_provider": "gemini", "configured_provider": "gemini",
                    "reason": "local judge",
                }):
            result = runner.run(
                repeats=1, api_url="http://127.0.0.1:8001",
                expected_provider="fixture",
                expected_build=BUILD,
            )
        self.assertEqual(result["api_url"], "http://127.0.0.1:8001/api")
        self.assertEqual(result["system_under_test"]["active_provider"], "fixture")
        self.assertEqual(result["system_under_test"]["health"], HEALTH)
        self.assertEqual(result["judge_provider"]["active_provider"], "gemini")
        self.assertEqual(result["provider"], HEALTH)  # compatibility alias


if __name__ == "__main__":
    unittest.main()
