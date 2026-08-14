"""Field Intelligence API — FastAPI, serves the built web app too."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func

from . import config
from .agent.orchestrator import analyze_audit, answer_clarification, review_finding
from .connectors import sources
from .connectors.places import summarise_themes
from .gateway import get_provider, provider_status
from .models import (Action, AuditLog, AuditSession, ClarificationQuestion,
                     EvidenceItem, Finding, Location, ModelCall, Observation,
                     SessionLocal, Standard, Tenant, Zone, init_db, uid)
from .seed import seed

app = FastAPI(title="Field Intelligence", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup() -> None:
    init_db()
    seed()


# ---------------- health / status ----------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, **config.key_status(), **provider_status()}


@app.get("/api/simulated")
def simulated_panel() -> dict:
    """The 'what's simulated' honesty panel.

    Counter-intuitively this raises trust in everything marked live, and it
    inoculates the demo against the one question that can sink it: "is this
    real?" Anything not listed here should be assumed to need a label.
    """
    live_maps = bool(config.GOOGLE_MAPS_API_KEY)
    live_llm = provider_status()["active_provider"] == "gemini"
    return {"elements": [
        {"element": "Wolf Creek location identity", "state": "PUBLIC_FACT",
         "note": "Independently confirmed against OpenStreetMap (relation 142995) — "
                 "street number and address tokens match."},
        {"element": "Audit standards", "state": "REPRESENTATIVE_DEMO_STANDARD",
         "note": "Real brand standards requested from BroadPeak; layer is configurable per tenant."},
        {"element": "Google review sample", "state": "LIVE_API" if live_maps else "DEMO_FIXTURE",
         "note": "Max ~5 Google-selected reviews. One sample carries one provenance — "
                 "live and fixture reviews are never blended."},
        {"element": "OpenStreetMap place facts", "state": "LIVE_API",
         "note": "Keyless and free. © OpenStreetMap contributors, ODbL 1.0."},
        {"element": "Public-web review collection", "state": "DISABLED_BY_DEFAULT",
         "note": "Built and quarantined (ADR-010). Returns nothing on this location; "
                 "the browser it needs is not in the deployed image."},
        {"element": "LLM analysis", "state": "LIVE_API (gemini-2.5-flash)" if live_llm else "DETERMINISTIC_FIXTURE_ENGINE"},
        {"element": "Photo description (vision)",
         "state": "LIVE_API" if live_llm else "UNAVAILABLE_BY_DESIGN",
         "note": "No fixture stand-in exists for vision: a description of an image "
                 "nobody looked at would be indistinguishable from evidence."},
        {"element": "Adversarial challenge panel",
         "state": "LIVE_API" if (live_llm and config.ENABLE_CHALLENGE_PANEL) else
                  "DETERMINISTIC_RULES" if config.ENABLE_CHALLENGE_PANEL else "DISABLED",
         "note": "Three independent challengers per candidate finding; vote counting is deterministic."},
        {"element": "Prior audit history (recurrence)", "state": "DEMO_FIXTURE",
         "note": "Seeded as real rows in the real tables, so the recurrence detector reads "
                 "the same data the live pipeline writes."},
        {"element": "EV depot tenant", "state": "DEMO_FIXTURE",
         "note": "Illustrative second tenant proving the multi-tenant engine."},
        {"element": "Action verification photos", "state": "SIMULATED_OUTCOME"},
    ]}


# ---------------- tenants / locations ----------------

@app.get("/api/tenants")
def tenants() -> list[dict]:
    db = SessionLocal()
    out = []
    for t in db.query(Tenant).all():
        locs = db.query(Location).filter_by(tenant_id=t.id).all()
        out.append({"id": t.id, "name": t.name, "kind": t.kind,
                    "locations": [{"id": l.id, "name": l.name, "address": l.address,
                                   "meta": l.meta} for l in locs]})
    db.close()
    return out


@app.get("/api/locations/{location_id}/zones")
def zones(location_id: str) -> list[dict]:
    db = SessionLocal()
    zs = db.query(Zone).filter_by(location_id=location_id).all()
    out = [{"id": z.id, "name": z.name, "required": z.required,
            "privacy_level": z.privacy_level} for z in zs]
    db.close()
    return out


@app.get("/api/locations/{location_id}/standards")
def standards(location_id: str) -> list[dict]:
    db = SessionLocal()
    loc = db.get(Location, location_id)
    if loc is None:
        db.close()
        raise HTTPException(404)
    ss = db.query(Standard).filter_by(tenant_id=loc.tenant_id, active=True).all()
    out = [{"id": s.id, "code": s.code, "category": s.category, "text": s.text,
            "severity_default": s.severity_default, "source_label": s.source_label} for s in ss]
    db.close()
    return out


@app.get("/api/locations/{location_id}/digital-truth")
def digital_truth(location_id: str) -> dict:
    """Digital-truth monitor: cross-channel fact conflicts, framed as opportunity.

    POC ships the verified Wolf Creek conflict as CACHED_LIVE_DATA (re-verified
    2026-08-13 against both live pages). The production version re-fetches per
    the source-registry refresh policy."""
    path = config.FIXTURES_DIR / f"digital_truth_{location_id.split('-')[0]}.json"
    alt = config.FIXTURES_DIR / "digital_truth_wolfcreek.json"
    if location_id == "wolf-creek-atlanta" and alt.exists():
        return json.loads(alt.read_text())
    return {"card_type": "OPPORTUNITY_DIGITAL_TRUTH", "conflicts": [],
            "note": "No digital-truth conflicts recorded for this location."}


@app.get("/api/locations/{location_id}/sources")
def location_sources(location_id: str) -> dict:
    """Every signal source, queried in parallel, with each one's outcome.

    Deliberately shows failures. "Three of four sources answered, here is which
    one and why" is information a reviewer is entitled to; a single silent
    fallback that looks like success is how fixture data gets mistaken for live.
    """
    db = SessionLocal()
    loc = db.get(Location, location_id)
    db.close()
    if loc is None:
        raise HTTPException(404)
    return sources.gather(location_id)


@app.get("/api/locations/{location_id}/signals")
def signals(location_id: str) -> dict:
    db = SessionLocal()
    loc = db.get(Location, location_id)
    db.close()
    if loc is None:
        raise HTTPException(404)
    return summarise_themes(location_id, loc.tenant_id)


# ---------------- audits ----------------

class AuditCreate(BaseModel):
    tenant_id: str
    location_id: str
    consultant_name: str = "Field Consultant"


class ObservationCreate(BaseModel):
    kind: str = "NOTE"          # NOTE | CHECKLIST | PHOTO_DESCRIPTION
    text: str
    zone_id: str | None = None


class ChecklistSubmit(BaseModel):
    responses: list[dict]        # [{item, response, zone_id}]


class AnswerBody(BaseModel):
    answer: str


class ReviewBody(BaseModel):
    action: str                  # approve | edit_approve | reject | dispute | request_evidence
    reviewer: str = "Reviewer"
    reason: str = ""
    edits: dict | None = None


@app.post("/api/audits")
def create_audit(body: AuditCreate) -> dict:
    db = SessionLocal()
    a = AuditSession(id=uid("audit"), tenant_id=body.tenant_id,
                     location_id=body.location_id, consultant_name=body.consultant_name)
    db.add(a)
    db.commit()
    out = {"id": a.id, "status": a.status}
    db.close()
    return out


@app.get("/api/audits/{audit_id}")
def get_audit(audit_id: str) -> dict:
    db = SessionLocal()
    a = db.get(AuditSession, audit_id)
    if a is None:
        db.close()
        raise HTTPException(404)
    obs = db.query(Observation).filter_by(audit_id=audit_id).all()
    qs = db.query(ClarificationQuestion).filter_by(audit_id=audit_id).all()
    fs = db.query(Finding).filter_by(audit_id=audit_id).all()
    acts = db.query(Action).filter(Action.finding_id.in_([f.id for f in fs])).all() if fs else []
    evidence = {e.id: {"id": e.id, "excerpt": e.excerpt, "provenance": e.provenance,
                       "source_type": e.source_type, "trust_class": e.trust_class}
                for e in db.query(EvidenceItem).filter_by(location_id=a.location_id).all()}
    std = {s.id: {"code": s.code, "text": s.text, "category": s.category}
           for s in db.query(Standard).filter_by(tenant_id=a.tenant_id).all()}
    out = {
        "id": a.id, "tenant_id": a.tenant_id, "location_id": a.location_id,
        "status": a.status, "consultant_name": a.consultant_name,
        "checklist_responses": a.checklist_responses,
        "observations": [{"id": o.id, "kind": o.kind, "text": o.text, "zone_id": o.zone_id,
                          "provenance": o.provenance, "payload": o.payload or {}} for o in obs],
        "questions": [{"id": q.id, "observation_id": q.observation_id, "question": q.question,
                       "why_needed": q.why_needed, "options": q.options, "answer": q.answer,
                       "status": q.status} for q in qs],
        "findings": [{"id": f.id, "lane": f.lane, "category": f.category, "title": f.title,
                      "status": f.status, "severity": f.severity, "confidence": f.confidence,
                      "consultant_statement": f.consultant_statement,
                      "model_interpretation": f.model_interpretation,
                      "uncertainty_reasons": f.uncertainty_reasons,
                      "not_supported": f.not_supported,
                      "standard": std.get(f.standard_id),
                      "evidence": [evidence.get(e) for e in f.evidence_ids if e in evidence],
                      "recommended_action": f.recommended_action,
                      "reasoning_trace": f.reasoning_trace or [],
                      "recurrence": f.recurrence or {},
                      "challenge_record": f.challenge_record or {},
                      "review_history": f.review_history} for f in fs],
        "actions": [{"id": x.id, "finding_id": x.finding_id, "description": x.description,
                     "owner_role": x.owner_role, "due_date": x.due_date,
                     "verification_method": x.verification_method, "status": x.status,
                     "events": x.events} for x in acts],
    }
    db.close()
    return out


@app.post("/api/audits/{audit_id}/observations")
def add_observation(audit_id: str, body: ObservationCreate) -> dict:
    db = SessionLocal()
    a = db.get(AuditSession, audit_id)
    if a is None:
        db.close()
        raise HTTPException(404)
    o = Observation(id=uid("ob"), tenant_id=a.tenant_id, audit_id=audit_id,
                    kind=body.kind, text=body.text, zone_id=body.zone_id)
    db.add(o)
    db.commit()
    out = {"id": o.id}
    db.close()
    return out


MAX_PHOTO_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}


@app.post("/api/audits/{audit_id}/photo")
async def add_photo(audit_id: str, file: UploadFile = File(...),
                    zone_id: str | None = Form(None)) -> dict:
    """Photo → described observation. The description is evidence, not a verdict.

    The vision model produces neutral description only; the resulting
    observation then goes through the same investigate → decide → human-approval
    path as a typed note. Nothing here can create a finding.
    """
    db = SessionLocal()
    a = db.get(AuditSession, audit_id)
    if a is None:
        db.close()
        raise HTTPException(404)
    zone = db.get(Zone, zone_id) if zone_id else None
    zone_name = zone.name if zone else ""
    privacy = zone.privacy_level if zone else "NORMAL"

    raw = await file.read()
    if len(raw) > MAX_PHOTO_BYTES:
        db.close()
        raise HTTPException(413, f"image exceeds {MAX_PHOTO_BYTES // (1024*1024)}MB")
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_IMAGE_TYPES:
        db.close()
        raise HTTPException(415, f"unsupported image type '{mime}'; "
                                 f"allowed: {sorted(ALLOWED_IMAGE_TYPES)}")

    try:
        desc = get_provider().describe_image(
            image_bytes=raw, mime_type=mime, zone_hint=zone_name,
            privacy_level=privacy, tenant_id=a.tenant_id, audit_id=audit_id)
    except Exception as e:
        db.close()
        # Surfaced, never faked. Fixture mode lands here on purpose.
        raise HTTPException(503, f"vision unavailable: {e}")

    digest = hashlib.sha256(raw).hexdigest()
    stored = config.UPLOADS_DIR / f"{digest}{Path(file.filename or '').suffix or '.jpg'}"
    if not stored.exists():
        stored.write_bytes(raw)

    if not desc.usable_as_evidence:
        db.close()
        return {"accepted": False, "reason": desc.unusable_reason,
                "people_visible": desc.people_visible,
                "image_quality_issues": desc.image_quality_issues,
                "note": "No observation was created. An unusable photo is a result, not a failure."}

    text = desc.description
    if desc.visible_facts:
        text += " Visible: " + "; ".join(desc.visible_facts) + "."
    if desc.legible_text:
        text += " Text in frame (transcribed, not interpreted): " + \
                " | ".join(f'"{t}"' for t in desc.legible_text) + "."

    o = Observation(id=uid("ob"), tenant_id=a.tenant_id, audit_id=audit_id,
                    kind="PHOTO_DESCRIPTION", text=text, zone_id=zone_id,
                    provenance="MODEL_DESCRIBED_PHOTO",
                    payload={"image_sha256": digest, "mime": mime, "bytes": len(raw),
                             "zone": zone_name, "zone_privacy_level": privacy,
                             "declined_to_assert": desc.declined_to_assert,
                             "image_quality_issues": desc.image_quality_issues,
                             "people_visible": desc.people_visible,
                             "vision_model": config.LLM_MODEL})
    ev = EvidenceItem(id=uid("ev"), tenant_id=a.tenant_id, location_id=a.location_id,
                      source_type="PHOTO", collection_method="UPLOAD",
                      provenance="MODEL_DESCRIBED_PHOTO", trust_class="OFFICIAL_OWNED",
                      excerpt=text[:600],
                      payload={"observation_id": o.id, "image_sha256": digest})
    db.add_all([o, ev])
    db.add(AuditLog(id=uid("log"), tenant_id=a.tenant_id, actor="MODEL",
                    entity_type="observation", entity_id=o.id, event="PHOTO_DESCRIBED",
                    detail={"image_sha256": digest, "zone": zone_name,
                            "declined_to_assert": desc.declined_to_assert,
                            "people_visible": desc.people_visible}))
    db.commit()
    out = {"accepted": True, "observation_id": o.id, "text": text,
           "image_sha256": digest, "declined_to_assert": desc.declined_to_assert,
           "image_quality_issues": desc.image_quality_issues,
           "people_visible": desc.people_visible, "zone_privacy_level": privacy,
           "provenance": "MODEL_DESCRIBED_PHOTO"}
    db.close()
    return out


@app.get("/api/photos/{digest}")
def get_photo(digest: str):
    """Serve an uploaded photo back for review. Digest-addressed, so the image a
    reviewer sees is provably the one the description was generated from."""
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise HTTPException(400, "bad digest")
    for p in config.UPLOADS_DIR.glob(f"{digest}.*"):
        return FileResponse(p)
    raise HTTPException(404)


@app.post("/api/audits/{audit_id}/checklist")
def submit_checklist(audit_id: str, body: ChecklistSubmit) -> dict:
    db = SessionLocal()
    a = db.get(AuditSession, audit_id)
    if a is None:
        db.close()
        raise HTTPException(404)
    a.checklist_responses = body.responses
    # failing checklist items become observations so they flow through analysis
    created = []
    for r in body.responses:
        if str(r.get("response", "")).lower() in {"fail", "no", "issue"}:
            o = Observation(id=uid("ob"), tenant_id=a.tenant_id, audit_id=audit_id,
                            kind="CHECKLIST", zone_id=r.get("zone_id"),
                            text=f"Checklist item failed: {r.get('item')} — {r.get('detail', 'no detail given')}")
            db.add(o)
            created.append(o.id)
    db.commit()
    db.close()
    return {"observations_created": created}


@app.post("/api/audits/{audit_id}/analyze")
def analyze(audit_id: str) -> dict:
    try:
        return analyze_audit(audit_id)
    except RuntimeError as e:
        raise HTTPException(429, str(e))


@app.get("/api/audits/{audit_id}/trace")
def audit_trace(audit_id: str) -> dict:
    """How the agent investigated this audit: every tool call, in order.

    Separate from the findings because the trace is a property of the run, not
    of any one conclusion — including the runs that concluded nothing.
    """
    db = SessionLocal()
    logs = (db.query(AuditLog)
              .filter_by(entity_id=audit_id, event="INVESTIGATE")
              .order_by(AuditLog.created_at.desc()).all())
    db.close()
    return {"runs": [{"at": l.created_at.isoformat(), **(l.detail or {})} for l in logs]}


@app.post("/api/questions/{question_id}/answer")
def answer_q(question_id: str, body: AnswerBody) -> dict:
    return answer_clarification(question_id, body.answer)


@app.post("/api/findings/{finding_id}/review")
def review(finding_id: str, body: ReviewBody) -> dict:
    return review_finding(finding_id, action=body.action, reviewer=body.reviewer,
                          edits=body.edits, reason=body.reason)


class VerifyBody(BaseModel):
    evidence_description: str
    verified_by: str = "Manager"


@app.post("/api/actions/{action_id}/verify")
def verify_action(action_id: str, body: VerifyBody) -> dict:
    db = SessionLocal()
    x = db.get(Action, action_id)
    if x is None:
        db.close()
        raise HTTPException(404)
    x.status = "VERIFIED"
    x.events = [*x.events, {"at": datetime.now(timezone.utc).isoformat(),
                            "event": "VERIFIED", "by": body.verified_by,
                            "evidence": body.evidence_description,
                            "provenance": "SIMULATED_OUTCOME" if config.APP_DEMO_MODE else "UPLOADED_DOCUMENT"}]
    db.commit()
    out = {"id": x.id, "status": x.status}
    db.close()
    return out


# ---------------- cost / observability console ----------------

@app.get("/api/console")
def console() -> dict:
    db = SessionLocal()
    calls = db.query(ModelCall).order_by(ModelCall.created_at.desc()).limit(50).all()
    agg = db.query(
        func.count(ModelCall.id), func.sum(ModelCall.input_tokens),
        func.sum(ModelCall.output_tokens), func.sum(ModelCall.est_cost_usd),
        func.avg(ModelCall.latency_ms)).one()
    audits = db.query(AuditSession).count()
    out = {
        "totals": {"calls": agg[0] or 0, "input_tokens": int(agg[1] or 0),
                   "output_tokens": int(agg[2] or 0),
                   "est_cost_usd": round(agg[3] or 0.0, 4),
                   "avg_latency_ms": int(agg[4] or 0),
                   "audits": audits,
                   "est_cost_per_audit": round((agg[3] or 0.0) / max(audits, 1), 4)},
        "recent": [{"purpose": c.purpose, "provider": c.provider, "model": c.model,
                    "in": c.input_tokens, "out": c.output_tokens,
                    "latency_ms": c.latency_ms, "cost": c.est_cost_usd,
                    "ok": c.ok, "retries": c.schema_retries,
                    "at": c.created_at.isoformat()} for c in calls],
    }
    db.close()
    return out


@app.get("/api/audit-log")
def audit_trail(entity_id: str | None = None) -> list[dict]:
    db = SessionLocal()
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if entity_id:
        q = q.filter_by(entity_id=entity_id)
    out = [{"at": x.created_at.isoformat(), "actor": x.actor, "entity_type": x.entity_type,
            "entity_id": x.entity_id, "event": x.event, "detail": x.detail}
           for x in q.limit(100).all()]
    db.close()
    return out


# ---------------- evals ----------------

@app.get("/api/evals")
def evals() -> dict:
    path = config.VAR_DIR / "eval_results.json"
    if not path.exists():
        return {"ran": False, "note": "Run `python -m server.evals.runner` to generate results."}
    return json.loads(path.read_text())


@app.post("/api/demo-reset")
def demo_reset() -> dict:
    """Restore the seeded demo state (spec §37)."""
    from .models import Base, engine
    Base.metadata.drop_all(engine)
    init_db()
    seed()
    return {"ok": True}


# ---------------- static frontend ----------------

_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
