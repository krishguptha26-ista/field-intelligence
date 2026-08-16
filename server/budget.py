"""Auditable, human-controlled per-visit model-call budget."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from . import config
from .models import AuditLog, ModelCall


BUDGET_EVENT = "LLM_BUDGET_ACKNOWLEDGED"


class ModelBudgetExceeded(RuntimeError):
    """Raised before a model invocation that would exceed the visit limit."""


def audit_budget(db, audit_id: str) -> dict:
    used = int(db.query(func.count(ModelCall.id)).filter_by(audit_id=audit_id).scalar() or 0)
    acknowledgements = int(
        db.query(func.count(AuditLog.id)).filter_by(
            entity_type="audit", entity_id=audit_id, event=BUDGET_EVENT
        ).scalar() or 0
    )
    limit = config.MAX_LLM_CALLS_PER_AUDIT + (
        acknowledgements * config.LLM_BUDGET_EXTENSION_CALLS
    )
    return {
        "used_calls": used,
        "base_limit": config.MAX_LLM_CALLS_PER_AUDIT,
        "limit_calls": limit,
        "remaining_calls": max(0, limit - used),
        "extension_calls": config.LLM_BUDGET_EXTENSION_CALLS,
        "acknowledgements": acknowledgements,
        "max_acknowledgements": config.MAX_LLM_BUDGET_ACKNOWLEDGEMENTS,
        "can_acknowledge": (
            used >= limit and acknowledgements < config.MAX_LLM_BUDGET_ACKNOWLEDGEMENTS
        ),
    }


def require_model_budget(audit_id: str | None) -> None:
    from .models import SessionLocal
    db = SessionLocal()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    hourly_used = int(db.query(func.count(ModelCall.id)).filter(
        ModelCall.created_at >= cutoff).scalar() or 0)
    if hourly_used >= config.MAX_LLM_CALLS_PER_HOUR:
        db.close()
        raise ModelBudgetExceeded(
            f"service-wide hourly analysis budget reached ({hourly_used}/"
            f"{config.MAX_LLM_CALLS_PER_HOUR} model calls); try again later"
        )
    if audit_id is None:
        db.close()
        return
    budget = audit_budget(db, audit_id)
    db.close()
    if budget["remaining_calls"] <= 0:
        raise ModelBudgetExceeded(
            f"per-audit analysis budget reached ({budget['used_calls']}/"
            f"{budget['limit_calls']} model calls); evidence already saved remains available"
        )
