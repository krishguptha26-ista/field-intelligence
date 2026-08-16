"""Single-process serialization for mutable POC workflows.

Render runs this demonstration with one Uvicorn process. Per-audit re-entrant
locks prevent successful concurrent requests from silently overwriting SQLite
JSON state or creating duplicate clarification/model work. A multi-process
production system must replace this with database row/advisory locks.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from threading import RLock


_AUDIT_LOCKS: defaultdict[str, RLock] = defaultdict(RLock)
_MODEL_WORKFLOW_LOCK = RLock()


@contextmanager
def audit_lock(audit_id: str | None):
    if not audit_id:
        yield
        return
    with _AUDIT_LOCKS[audit_id]:
        yield


@contextmanager
def model_workflow_lock():
    """Serialize provider calls so budget checks and ledgers stay atomic.

    This is intentionally conservative for the single-worker assessment POC.
    A horizontally scaled deployment must move reservations to the database.
    """
    with _MODEL_WORKFLOW_LOCK:
        yield
