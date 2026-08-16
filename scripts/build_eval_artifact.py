"""Generate a source-matched deterministic Eval Lab artifact for the image."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent.parent
API_URL = "http://127.0.0.1:8765/api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    build_db = (ROOT / "var" / "_build_eval.db").resolve()
    environment = os.environ.copy()
    environment.update({
        "APP_ENV": "development",
        "LLM_PROVIDER": "fixture",
        "DATABASE_URL": os.environ.get(
            "BUILD_EVAL_DATABASE_URL", f"sqlite:///{build_db.as_posix()}"
        ),
        "DEMO_USERNAME": "demo-user",
        "DEMO_PASSWORD": "Broadpeak-demo-user",
        "SESSION_SECRET": "build-only-eval-session-secret-not-used-in-runtime",
    })
    # The runner imports the same configuration modules in this process to
    # report the judge separately from the HTTP system under test.
    os.environ.update(environment)
    process = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "server.app:app",
        "--host", "127.0.0.1", "--port", "8765",
    ], cwd=ROOT, env=environment)
    try:
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                if httpx.get(f"{API_URL}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.25)
        else:
            raise RuntimeError("evaluation server did not become healthy")

        from server.evals.runner import run

        result = run(1, api_url=API_URL, expected_provider="fixture")
        result.setdefault("artifact", {}).update({
            "delivery": "docker_build_fixture",
            "scope": (
                "Deterministic source-matched pipeline evaluation. Cases requiring "
                "live vision or a semantic judge remain explicitly skipped."
            ),
        })
        destination = ROOT / "data" / "eval_results.json"
        destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        if not result.get("gate", {}).get("passed"):
            raise RuntimeError("unsupported-finding release gate failed")
        if any(not row.get("passed") and not row.get("skipped")
               for row in result.get("cases", [])):
            raise RuntimeError("one or more executable evaluation cases failed")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        runtime_artifact = ROOT / "var" / "eval_results.json"
        runtime_artifact.unlink(missing_ok=True)
        if "BUILD_EVAL_DATABASE_URL" not in os.environ:
            for candidate in build_db.parent.glob(f"{build_db.name}*"):
                candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
