"""Stable source identity for detecting stale evaluation servers."""
from __future__ import annotations

import hashlib
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
def _runtime_files() -> tuple[str, ...]:
    """Return every Python source file shipped in the server runtime.

    A hand-maintained shortlist allowed persistence, configuration and
    connector changes to retain an old fingerprint. Sorting the complete
    server tree keeps the identity deterministic and covers every executable
    backend change.
    """
    return tuple(
        path.relative_to(_ROOT).as_posix()
        for path in sorted((_ROOT / "server").rglob("*.py"))
    )


def source_fingerprint() -> str:
    """Hash the server code that defines the evaluated runtime contract.

    The application computes this once when its process imports ``server.app``.
    A local evaluator computes it from its checkout immediately before the
    health preflight. An old process therefore cannot masquerade as the current
    build merely because it listens on the expected port and uses the expected
    model provider.
    """
    digest = hashlib.sha256()
    for relative in _runtime_files():
        path = _ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]
