"""Field Intelligence API — FastAPI, serves the built web app too."""
from __future__ import annotations

import hashlib
import base64
import hmac
import json
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Literal
from threading import Lock

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, text as sql_text

from . import config
from .budget import BUDGET_EVENT, ModelBudgetExceeded, audit_budget
from .build_meta import source_fingerprint
from .agent.orchestrator import (analyze_audit, answer_clarification,
                                 challenge_existing_finding, review_finding)
from .connectors import sources
from .connectors.benchmark import competitor_benchmark
from .connectors.places import summarise_themes
from .connectors.review_snapshot import load_review_snapshot
from .gateway import get_provider, provider_status
from .locks import audit_lock
from .field_guide import ZONE_CHECK_CODES, issue_photo_policy
from .models import (Action, AuditLog, AuditSession, ClarificationQuestion, DemoAccessEvent,
                     EvidenceItem, Finding, Location, ModelCall, Observation,
                     OperationalTicket, TaxonomyProposal,
                     SessionLocal, Standard, Tenant, Zone, init_db, uid)
from .seed import seed
from .regulatory import (WOLF_CREEK_JURISDICTION, standard_metadata)

app = FastAPI(title="Field Intelligence", version=config.APP_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])
_BUILD_FINGERPRINT = source_fingerprint()

SESSION_COOKIE = "fieldintel_session"
_PUBLIC_API_PATHS = {"/api/health", "/api/auth/login", "/api/auth/session"}
_LOGIN_ATTEMPTS: defaultdict[str, deque[float]] = defaultdict(deque)
_LOGIN_ATTEMPTS_LOCK = Lock()
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_ATTEMPTS = 10


def _session_token(username: str) -> str:
    expires = int(time.time()) + config.SESSION_HOURS * 3600
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": username, "exp": expires}, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(
        config.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def _session_user(token: str | None) -> str | None:
    if not token or "." not in token or not config.SESSION_SECRET:
        return None
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(
        config.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode())
        if int(data.get("exp") or 0) <= int(time.time()):
            return None
        username = str(data.get("sub") or "").strip()
        return username if username == config.DEMO_USERNAME else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


@app.middleware("http")
async def require_demo_session(request: Request, call_next):
    if (not request.url.path.startswith("/api/")
            or request.method == "OPTIONS"
            or request.url.path in _PUBLIC_API_PATHS):
        return await call_next(request)
    user = _session_user(request.cookies.get(SESSION_COOKIE))
    if user is None:
        return JSONResponse(status_code=401, content={"detail": "sign in required"})
    request.state.user = user
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self'; object-src 'none'; img-src 'self' data: blob:; "
        "media-src 'self' blob:; connect-src 'self'; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if config.APP_ENV == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


_WORKFLOW_ROLE_CAPABILITIES = [
    {"role": "Field Consultant", "capture_evidence": True,
     "review_findings": False, "verify_resolution": False},
    {"role": "Reviewer", "capture_evidence": False,
     "review_findings": True, "verify_resolution": True},
    {"role": "Brand Leader", "capture_evidence": False,
     "review_findings": True, "verify_resolution": True},
    {"role": "Operations Manager", "capture_evidence": True,
     "review_findings": False, "verify_resolution": True},
]
_INDEPENDENT_VERIFIER_ROLES = tuple(
    row["role"] for row in _WORKFLOW_ROLE_CAPABILITIES
    if row["verify_resolution"]
)
_VERIFICATION_CONFLICT_EVENTS = {
    "VALIDATED_ON_SITE", "FINDING_REVIEW_VALIDATED",
    "RESOLUTION_SUBMITTED", "ACTION_RESOLUTION_SUBMITTED",
    "AFTER_EVIDENCE_UPLOADED",
}


def _linked_ticket_for_action(db, action: Action) -> OperationalTicket | None:
    return next((ticket for ticket in db.query(OperationalTicket).filter_by(
        tenant_id=action.tenant_id).all()
        if action.id in (ticket.source_refs or [])), None)


def _linked_action_for_ticket(db, ticket: OperationalTicket) -> Action | None:
    return next((db.get(Action, ref) for ref in (ticket.source_refs or [])
                 if str(ref).startswith("act_")), None)


def _verification_capabilities(*, ticket: OperationalTicket | None = None,
                               action: Action | None = None) -> dict:
    actors: dict[str, str] = {}
    for event in [*(ticket.events if ticket else []), *(action.events if action else [])]:
        if event.get("event") not in _VERIFICATION_CONFLICT_EVENTS:
            continue
        display = str(event.get("by") or "").strip()
        if display:
            actors.setdefault(display.casefold(), display)
    eligible_roles = [role for role in _INDEPENDENT_VERIFIER_ROLES
                      if role.casefold() not in actors]
    return {
        "requires_independent_verifier": True,
        "excluded_actors": sorted(actors.values(), key=str.casefold),
        "eligible_verifier_roles": eligible_roles,
        "independent_verifier_available": bool(eligible_roles),
        "identity_boundary": (
            "POC display-role enforcement; production requires authenticated user identity"
        ),
    }


@app.exception_handler(ModelBudgetExceeded)
def _model_budget_handler(_request, exc: ModelBudgetExceeded):
    return JSONResponse(status_code=429, content={"detail": str(exc)})


@app.on_event("startup")
def _startup() -> None:
    config.validate_runtime()
    init_db()
    seed()


# ---------------- health / status ----------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "build_fingerprint": _BUILD_FINGERPRINT,
            **config.key_status(), **provider_status()}


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=500)


def _demo_access_payload(request: Request) -> tuple[DemoAccessEvent, dict]:
    """Build an inspectable login event without retaining a raw IP address."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    source_address = forwarded or (request.client.host if request.client else "unknown")
    user_agent = request.headers.get("user-agent", "unknown")[:240]
    visitor_id = hmac.new(
        config.SESSION_SECRET.encode(),
        f"{source_address}|{user_agent}".encode(),
        hashlib.sha256,
    ).hexdigest()[:12]
    event = DemoAccessEvent(
        id=uid("access"),
        username=config.DEMO_USERNAME,
        client_fingerprint=visitor_id,
        user_agent=user_agent,
        notification_status=("PENDING" if config.LOGIN_NOTIFICATION_WEBHOOK_URL
                             else "NOT_CONFIGURED"),
        detail={"environment": config.APP_ENV, "raw_ip_stored": False},
    )
    occurred_at = datetime.now(timezone.utc).isoformat()
    text = (f"Field Intelligence demo login: {config.DEMO_USERNAME} at "
            f"{occurred_at} (visitor {visitor_id}, {config.APP_ENV}).")
    payload = {
        "event": "FIELDINTEL_DEMO_LOGIN",
        "message": text,
        "text": text,
        "occurred_at": occurred_at,
        "username": config.DEMO_USERNAME,
        "visitor_id": visitor_id,
        "user_agent": user_agent,
        "environment": config.APP_ENV,
        "app_url": os.getenv("RENDER_EXTERNAL_URL", ""),
        "privacy": "Raw IP and credentials are not included.",
    }
    return event, payload


def _deliver_login_notification(event_id: str | None, payload: dict) -> None:
    """Best-effort delivery after the login response; never blocks access."""
    status = "FAILED"
    error_kind = ""
    try:
        response = httpx.post(
            config.LOGIN_NOTIFICATION_WEBHOOK_URL,
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        status = "SENT"
    except Exception as exc:  # delivery state is visible; credentials are never logged
        error_kind = type(exc).__name__
    if not event_id:
        return
    db = SessionLocal()
    try:
        event = db.get(DemoAccessEvent, event_id)
        if event:
            event.notification_status = status
            if status == "SENT":
                event.notified_at = datetime.now(timezone.utc)
            event.detail = {**(event.detail or {}),
                            **({"delivery_error": error_kind} if error_kind else {})}
            db.commit()
    finally:
        db.close()


@app.get("/api/auth/session")
def auth_session(request: Request) -> dict:
    user = _session_user(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "sign in required")
    return {"authenticated": True, "username": user,
            "expires_in_hours": config.SESSION_HOURS}


@app.post("/api/auth/login")
def auth_login(body: LoginBody, request: Request, background_tasks: BackgroundTasks):
    client_key = "unknown"
    # Rate limiting is deliberately local to the single Render worker used by
    # this assessment. A scaled deployment must move it to a shared store.
    # The username is included so unrelated users behind one NAT do not share
    # the same bucket.
    client_key = body.username.strip().casefold()
    now = time.monotonic()
    with _LOGIN_ATTEMPTS_LOCK:
        attempts = _LOGIN_ATTEMPTS[client_key]
        while attempts and now - attempts[0] > _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, "too many sign-in attempts; try again in a few minutes")
        attempts.append(now)
    valid_user = hmac.compare_digest(body.username.strip(), config.DEMO_USERNAME)
    valid_password = bool(config.DEMO_PASSWORD) and hmac.compare_digest(
        body.password, config.DEMO_PASSWORD)
    if not (valid_user and valid_password):
        raise HTTPException(401, "invalid username or password")
    with _LOGIN_ATTEMPTS_LOCK:
        _LOGIN_ATTEMPTS.pop(client_key, None)
    access_event, notification = _demo_access_payload(request)
    persisted_event_id: str | None = None
    db = SessionLocal()
    try:
        db.add(access_event)
        db.commit()
        persisted_event_id = access_event.id
    except Exception:
        # Access telemetry must never become an authentication dependency.
        db.rollback()
    finally:
        db.close()
    if config.LOGIN_NOTIFICATION_WEBHOOK_URL:
        background_tasks.add_task(
            _deliver_login_notification, persisted_event_id, notification)
    response = JSONResponse({"authenticated": True,
                             "username": config.DEMO_USERNAME})
    response.set_cookie(
        SESSION_COOKIE,
        _session_token(config.DEMO_USERNAME),
        max_age=config.SESSION_HOURS * 3600,
        httponly=True,
        secure=config.APP_ENV == "production",
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    return response


@app.get("/api/workflow-capabilities")
def workflow_capabilities() -> dict:
    """Expose POC role affordances without pretending they are authentication."""
    return {
        "roles": _WORKFLOW_ROLE_CAPABILITIES,
        "verification_policy": {
            "requires_independent_verifier": True,
            "eligible_role_labels": list(_INDEPENDENT_VERIFIER_ROLES),
            "identity_boundary": (
                "Role labels drive the demo UI; production enforcement requires SSO/RBAC."
            ),
        },
    }


@app.get("/api/simulated")
def simulated_panel() -> dict:
    """The 'what's simulated' honesty panel.

    Counter-intuitively this raises trust in everything marked live, and it
    inoculates the demo against the one question that can sink it: "is this
    real?" Anything not listed here should be assumed to need a label.
    """
    live_maps = bool(config.GOOGLE_MAPS_API_KEY)
    llm_status = provider_status()
    live_llm = llm_status.get("readiness") == "LIVE_CALL_CONFIRMED"
    configured_llm = llm_status.get("configured_provider") == "gemini"
    return {"demo_reset_available": config.APP_ENV != "production", "elements": [
        {"element": "Wolf Creek location identity", "state": "PUBLIC_FACT",
         "note": "Independently confirmed against OpenStreetMap (relation 142995) — "
                 "street number and address tokens match."},
        {"element": "Audit standards", "state": "MIXED_SOURCED_GUIDANCE",
         "note": "Federal, Georgia, Fulton County, industry-BMP and venue-policy checks are source-labelled. BroadPeak supplied no controlled internal standard pack; field responses remain evidence for human review, not legal determinations."},
        {"element": "Google Places review source", "state": "CONFIGURED" if live_maps else "NOT_CONFIGURED",
         "note": "Places returns a small Google-selected sample. It remains visible diagnostically but is not selected over the complete assessment snapshot."},
        {"element": "OpenStreetMap place facts", "state": "LIVE_API",
         "note": "Keyless and free. © OpenStreetMap contributors, ODbL 1.0."},
        {"element": "Public-web review collection", "state": "DISABLED_BY_DEFAULT",
         "note": "Live collection is quarantined and never runs in a page request."},
        {"element": "Wolf Creek review intelligence snapshot", "state": "SCRAPED_PUBLIC_WEB",
         "note": "362-row one-off assessment snapshot; reviewer identity removed; locally filtered by date and rating."},
        {"element": "Atlanta competitor benchmark", "state": "SCRAPED_PUBLIC_WEB_AGGREGATE",
         "note": "1,235 anonymized comparator reviews across three nearby public courses; directional cohort, not a market claim."},
        {"element": "LLM analysis", "state": ("LIVE_API (gemini-2.5-flash)" if live_llm
                                                else "CONFIGURED_NOT_PROBED" if configured_llm
                                                else "DETERMINISTIC_FIXTURE_ENGINE")},
        {"element": "Photo description (vision)",
         "state": "LIVE_API" if live_llm else "CONFIGURED_NOT_PROBED" if configured_llm else "UNAVAILABLE_BY_DESIGN",
         "note": "No fixture stand-in exists for vision: a description of an image "
                 "nobody looked at would be indistinguishable from evidence."},
        {"element": "Adversarial challenge panel",
         "state": "ON_DEMAND_REVIEW" if (config.ENABLE_CHALLENGE_PANEL and
                                             not config.CHALLENGE_PANEL_DURING_CAPTURE) else
                  "LIVE_API" if (live_llm and config.ENABLE_CHALLENGE_PANEL) else
                  "DETERMINISTIC_RULES" if config.ENABLE_CHALLENGE_PANEL else "DISABLED",
         "note": "Three independent review lenses run on reviewer request to keep field capture responsive; unavailable lenses fail closed. Vote counting is deterministic."},
        {"element": "Prior audit history (recurrence)", "state": "DEMO_FIXTURE",
         "note": "Seeded as real rows in the real tables, so the recurrence detector reads "
                 "the same data the live pipeline writes."},
        {"element": "EV depot tenant", "state": "DEMO_FIXTURE",
         "note": "Illustrative second tenant with enforced tenant/location relationships. Authentication remains a production requirement."},
        {"element": "Operational ticket before/after photos", "state": "STAFF_UPLOADED_PHOTO",
         "note": "Real validated image files in the POC; identity/authentication remains out of scope."},
        {"element": "Legacy action verification description", "state": "SIMULATED_OUTCOME"},
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
    if db.get(Location, location_id) is None:
        db.close()
        raise HTTPException(404, "location not found")
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
            "severity_default": s.severity_default, "source_label": s.source_label,
            **standard_metadata(s.code)} for s in ss]
    db.close()
    return out


@app.get("/api/locations/{location_id}/field-guide")
def field_guide(location_id: str) -> dict:
    """A server-owned guided inspection, not arbitrary client checklist text."""
    db = SessionLocal()
    loc = db.get(Location, location_id)
    if loc is None:
        db.close()
        raise HTTPException(404, "location not found")
    standards_by_code = {
        s.code: s for s in db.query(Standard).filter_by(
            tenant_id=loc.tenant_id, active=True).all()
    }
    guide_zones = []
    for zone in db.query(Zone).filter_by(location_id=location_id).all():
        checks = []
        for code in ZONE_CHECK_CODES.get(zone.name, []):
            standard = standards_by_code.get(code)
            if standard:
                checks.append({
                    "id": f"{zone.id}:{standard.id}",
                    "standard_id": standard.id,
                    "standard_code": standard.code,
                    "category": standard.category,
                    "question": standard.text,
                    "severity_default": standard.severity_default,
                    "source_label": standard.source_label,
                    "authoritative": False,
                    "photo_policy": issue_photo_policy(
                        standard.code,
                        category=standard.category,
                        severity=standard.severity_default,
                    ),
                    **standard_metadata(standard.code),
                })
        guide_zones.append({
            "id": zone.id, "name": zone.name, "required": zone.required,
            "privacy_level": zone.privacy_level, "checks": checks,
        })
    db.close()
    is_wolf_creek = location_id == "wolf-creek-atlanta"
    return {
        "mode": "SOURCED_DISCOVERY_GUIDE" if is_wolf_creek else "DISCOVERY_GUIDE",
        "authority": "MIXED_SOURCED_GUIDANCE" if is_wolf_creek else "NON_AUTHORITATIVE_DEMO_STANDARD",
        "title": "Wolf Creek sourced field guide" if is_wolf_creek else "Representative field guide",
        "disclaimer": (
            "Official external sources, conditional requirements, Georgia golf BMPs and "
            "representative operating prompts are labelled separately. A field response "
            "creates evidence for human review—not a legal conclusion. BroadPeak has not "
            "yet supplied its controlled internal standards."
            if is_wolf_creek else
            "BroadPeak supplied no standards or checklist. These representative POC checks "
            "guide discovery but cannot establish an authoritative compliance violation."
        ),
        "jurisdiction": WOLF_CREEK_JURISDICTION if is_wolf_creek else None,
        "zones": guide_zones,
    }


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
    tenant_id: str = Field(min_length=1, max_length=100)
    location_id: str = Field(min_length=1, max_length=100)
    consultant_name: str = Field(default="Field Consultant", min_length=2, max_length=120)


class AuditDeleteBody(BaseModel):
    confirm_audit_id: str = Field(min_length=1, max_length=100)
    requested_by: str = Field(min_length=2, max_length=120)


class ObservationCreate(BaseModel):
    # Provenance-sensitive kinds (CHECKLIST, PHOTO_DESCRIPTION, VOICE_TRANSCRIPT,
    # VIDEO_DESCRIPTION) are server-assigned by their dedicated endpoints.
    kind: Literal["NOTE", "WRITTEN_PHOTO_DESCRIPTION"] = "NOTE"
    text: str = Field(min_length=1, max_length=10000)
    zone_id: str | None = None

    @field_validator("text")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("observation text must not be blank")
        return value


class ChecklistResponse(BaseModel):
    item: str = Field(min_length=1, max_length=500)
    standard_code: str = Field(min_length=1, max_length=80)
    response: Literal["PASS", "FAIL", "NOT_APPLICABLE"]
    detail: str = Field(default="", max_length=3000)
    zone_id: str | None = None
    evidence_observation_ids: list[str] = Field(default_factory=list, max_length=20)
    photo_decision: Literal["ATTACHED", "CONTINUE_WITHOUT_PHOTO"] | None = None

    @field_validator("response", mode="before")
    @classmethod
    def normalise_response(cls, value):
        token = str(value).strip().lower()
        if token in {"fail", "failed", "no", "issue", "false", "noncompliant"}:
            return "FAIL"
        if token in {"pass", "passed", "yes", "true", "compliant"}:
            return "PASS"
        if token in {"n/a", "na", "not applicable", "not_applicable"}:
            return "NOT_APPLICABLE"
        return value

    @model_validator(mode="after")
    def require_issue_detail(self):
        if self.response == "FAIL" and len(self.detail.strip()) < 5:
            raise ValueError("a failed check requires a specific observed condition")
        if self.response == "NOT_APPLICABLE" and len(self.detail.strip()) < 5:
            raise ValueError("a not-applicable check requires an applicability reason")
        return self


class ChecklistSubmit(BaseModel):
    responses: list[ChecklistResponse] = Field(min_length=1, max_length=500)


class AnswerBody(BaseModel):
    answer: str = Field(min_length=1, max_length=5000)

    @field_validator("answer")
    @classmethod
    def non_blank_answer(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("answer must not be blank")
        return value


class ObservationConfirmBody(BaseModel):
    text: str = Field(min_length=3, max_length=20000)

    @field_validator("text")
    @classmethod
    def confirmed_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("confirmed observation text is too short")
        return value


@app.post("/api/observations/{observation_id}/confirm")
def confirm_observation(observation_id: str, body: ObservationConfirmBody) -> dict:
    """Consultant confirms/edits a model transcript before audit analysis."""
    db = SessionLocal()
    observation = db.get(Observation, observation_id)
    if observation is None:
        db.close()
        raise HTTPException(404, "observation not found")
    audit = db.get(AuditSession, observation.audit_id)
    _ensure_audit_mutable(db, audit)
    if observation.kind != "VOICE_TRANSCRIPT":
        db.close()
        raise HTTPException(409, "only voice transcripts require this confirmation")
    payload = dict(observation.payload or {})
    if not payload.get("awaiting_confirmation"):
        out = {"id": observation.id, "confirmed": True, "idempotent": True,
               "audit_id": observation.audit_id}
        db.close()
        return out
    payload["model_transcript"] = payload.get("transcript", "")
    payload["confirmed_text"] = body.text
    payload["awaiting_confirmation"] = False
    payload["verification_state"] = "CONSULTANT_REPORTED"
    observation.text = f"Consultant voice note (confirmed transcript): {body.text}"
    observation.payload = payload
    db.add(AuditLog(
        id=uid("log"), tenant_id=observation.tenant_id, actor="CONSULTANT",
        entity_type="observation", entity_id=observation.id,
        event="VOICE_TRANSCRIPT_CONFIRMED",
        detail={"model_transcript_retained": True},
    ))
    db.commit()
    out = {"id": observation.id, "confirmed": True, "idempotent": False,
           "audit_id": observation.audit_id}
    db.close()
    return out


class ReviewBody(BaseModel):
    action: Literal["approve", "edit_approve", "reject", "dispute", "request_evidence"]
    reviewer: str = Field(default="Reviewer", min_length=2, max_length=120)
    reason: str = Field(default="", max_length=3000)
    edits: dict | None = None

    @model_validator(mode="after")
    def validate_review(self):
        if self.action in {"reject", "dispute", "request_evidence"} and not self.reason.strip():
            raise ValueError(f"{self.action} requires a reason")
        if self.action == "edit_approve":
            if not self.edits:
                raise ValueError("edit_approve requires edits")
            allowed = {"title", "severity", "model_interpretation", "recommended_action"}
            unknown = set(self.edits) - allowed
            if unknown:
                raise ValueError(f"unsupported edit fields: {sorted(unknown)}")
            severity = self.edits.get("severity")
            if severity and severity not in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                raise ValueError("invalid severity")
        return self


class ChallengeBody(BaseModel):
    reviewer: Literal["Reviewer", "Brand Leader"]


class AuditSubmitBody(BaseModel):
    submitted_by: str = Field(default="Field Consultant", min_length=2, max_length=120)
    no_issue_attestation: bool = False


class BudgetAcknowledgeBody(BaseModel):
    acknowledged_by: str = Field(default="Field Consultant", min_length=2, max_length=120)
    reason: str = Field(min_length=8, max_length=1000)
    request_id: str = Field(min_length=8, max_length=120)


IMMUTABLE_AUDIT_STATUSES = {"READY_FOR_REVIEW", "SUBMITTED", "COMPLETE"}


def _ensure_audit_mutable(db, audit: AuditSession | None) -> None:
    if audit is not None and audit.status in IMMUTABLE_AUDIT_STATUSES:
        db.close()
        raise HTTPException(
            409,
            "submitted audit is immutable; start a separate walkthrough to add evidence",
        )


@app.post("/api/audits")
def create_audit(body: AuditCreate) -> dict:
    db = SessionLocal()
    tenant = db.get(Tenant, body.tenant_id)
    location = db.get(Location, body.location_id)
    if tenant is None or location is None:
        db.close()
        raise HTTPException(404, "tenant or location not found")
    if location.tenant_id != tenant.id:
        db.close()
        raise HTTPException(422, "location does not belong to tenant")
    a = AuditSession(id=uid("audit"), tenant_id=body.tenant_id,
                     location_id=body.location_id, consultant_name=body.consultant_name)
    db.add(a)
    db.commit()
    out = {"id": a.id, "status": a.status}
    db.close()
    return out


@app.get("/api/audits")
def list_audits(tenant_id: str, location_id: str) -> list[dict]:
    """Recent visits for the explicit tenant/location context."""
    db = SessionLocal()
    location = db.get(Location, location_id)
    if location is None or location.tenant_id != tenant_id:
        db.close()
        raise HTTPException(404, "tenant/location context not found")
    rows = (db.query(AuditSession).filter_by(
        tenant_id=tenant_id, location_id=location_id,
    ).order_by(AuditSession.created_at.desc()).limit(25).all())
    out = [{
        "id": row.id,
        "status": row.status,
        "consultant_name": row.consultant_name,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "checklist_responses": len(row.checklist_responses or []),
        "can_discard": (
            config.APP_ENV != "production"
            and row.status not in IMMUTABLE_AUDIT_STATUSES
        ),
    } for row in rows]
    db.close()
    return out


@app.delete("/api/audits/{audit_id}")
def discard_audit(audit_id: str, body: AuditDeleteBody) -> dict:
    """Discard one unfinished local/demo visit after exact-id confirmation.

    Submitted packets are audit records and cannot be deleted from this POC.
    Production has no unauthenticated delete path; SSO/RBAC and retention policy
    must own that decision there.
    """
    if config.APP_ENV == "production":
        raise HTTPException(404)
    if body.confirm_audit_id != audit_id:
        raise HTTPException(422, "confirmation does not match the visit being discarded")
    db = SessionLocal()
    audit = db.get(AuditSession, audit_id)
    if audit is None:
        db.close()
        raise HTTPException(404, "visit not found")
    if audit.status in IMMUTABLE_AUDIT_STATUSES:
        db.close()
        raise HTTPException(
            409,
            "submitted review packets are immutable; start a new visit instead",
        )

    observations = db.query(Observation).filter_by(audit_id=audit_id).all()
    findings = db.query(Finding).filter_by(audit_id=audit_id).all()
    questions = db.query(ClarificationQuestion).filter_by(audit_id=audit_id).all()
    observation_ids = {row.id for row in observations}
    finding_ids = {row.id for row in findings}
    question_ids = {row.id for row in questions}
    actions = (db.query(Action).filter(Action.finding_id.in_(finding_ids)).all()
               if finding_ids else [])
    action_ids = {row.id for row in actions}
    related_refs = observation_ids | finding_ids | action_ids
    tickets = [row for row in db.query(OperationalTicket).filter_by(
        tenant_id=audit.tenant_id, location_id=audit.location_id).all()
        if related_refs.intersection(row.source_refs or [])]
    ticket_ids = {row.id for row in tickets}
    evidence = [row for row in db.query(EvidenceItem).filter_by(
        tenant_id=audit.tenant_id, location_id=audit.location_id).all()
        if (row.payload or {}).get("observation_id") in observation_ids]
    digests = {
        digest for row in evidence
        for digest in ((row.payload or {}).get("image_sha256"),
                       (row.payload or {}).get("media_sha256"))
        if digest
    }
    entity_ids = ({audit_id} | observation_ids | finding_ids | question_ids |
                  action_ids | ticket_ids)

    for row in actions + tickets + evidence + findings + questions + observations:
        db.delete(row)
    db.query(ModelCall).filter_by(audit_id=audit_id).delete(
        synchronize_session=False)
    if entity_ids:
        db.query(AuditLog).filter(AuditLog.entity_id.in_(entity_ids)).delete(
            synchronize_session=False)
    db.delete(audit)
    db.commit()

    # Remove an orphaned upload only when no remaining evidence envelope refers
    # to its digest. A repeated upload used by another visit is retained.
    remaining_evidence = db.query(EvidenceItem).all()
    remaining_digests = {
        digest for row in remaining_evidence
        for digest in ((row.payload or {}).get("image_sha256"),
                       (row.payload or {}).get("media_sha256"))
        if digest
    }
    removed_files = 0
    for digest in digests - remaining_digests:
        for path in config.UPLOADS_DIR.glob(f"{digest}.*"):
            if path.is_file():
                path.unlink()
                removed_files += 1
    db.close()
    return {
        "discarded": audit_id,
        "requested_by": body.requested_by,
        "removed_files": removed_files,
    }


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
    field_tickets = [ticket for ticket in db.query(OperationalTicket).filter_by(
        location_id=a.location_id).all()
        if ticket.source_kind in {"PHOTO_BACKED_FIELD_FINDING",
                                  "UNMAPPED_PHOTO_BACKED_FIELD_CONCERN"}
        and any(ref in ({f.id for f in fs} | {o.id for o in obs})
                for ref in (ticket.source_refs or []))]
    acts = db.query(Action).filter(Action.finding_id.in_([f.id for f in fs])).all() if fs else []
    observation_by_id = {row.id: row for row in obs}

    def consultant_statement_display(finding: Finding) -> str:
        history = sorted([
            row for row in qs
            if row.observation_id == finding.observation_id and row.answer
            and not (row.why_needed or "").startswith(
                ("PHOTO_REQUIRED:", "PHOTO_RECOMMENDED:",
                 "UNMAPPED_PHOTO_REQUIRED:"))
        ], key=lambda row: row.created_at)
        if len(history) <= 2:
            return finding.consultant_statement
        source = observation_by_id.get(finding.observation_id)
        if source is None:
            return finding.consultant_statement
        # Legacy audits may contain pre-cap repetitive turns. Preserve every
        # raw answer in the question/audit history while presenting the first
        # clarifying fact and final settling fact in the field/review packet.
        return "\n".join([
            source.text,
            f"Clarification 1: {history[0].answer}",
            f"Clarification 2: {history[-1].answer}",
        ])
    evidence = {e.id: {"id": e.id, "excerpt": e.excerpt, "provenance": e.provenance,
                       "source_type": e.source_type, "trust_class": e.trust_class,
                       "payload": e.payload or {}}
                for e in db.query(EvidenceItem).filter_by(location_id=a.location_id).all()}
    std = {s.id: {"code": s.code, "text": s.text, "category": s.category,
                  "source_label": s.source_label, "authoritative": False,
                  **standard_metadata(s.code)}
           for s in db.query(Standard).filter_by(tenant_id=a.tenant_id).all()}
    def finding_standard(finding: Finding) -> dict | None:
        for trace_item in finding.reasoning_trace or []:
            if trace_item.get("tool") == "standard_snapshot":
                snapshot = trace_item.get("result")
                if isinstance(snapshot, dict):
                    return snapshot
        return std.get(finding.standard_id)
    out = {
        "id": a.id, "tenant_id": a.tenant_id, "location_id": a.location_id,
        "status": a.status, "consultant_name": a.consultant_name,
        "checklist_responses": a.checklist_responses,
        "observations": [{"id": o.id, "kind": o.kind, "text": o.text, "zone_id": o.zone_id,
                          "provenance": o.provenance, "payload": o.payload or {}} for o in obs],
        "questions": [{"id": q.id, "observation_id": q.observation_id, "question": q.question,
                       "why_needed": q.why_needed, "options": q.options, "answer": q.answer,
                       "status": q.status,
                       "response_type": (
                           "PHOTO" if q.why_needed.startswith(
                               ("PHOTO_REQUIRED:", "UNMAPPED_PHOTO_REQUIRED:"))
                           else "PHOTO_RECOMMENDED" if q.why_needed.startswith(
                               "PHOTO_RECOMMENDED:")
                           else "TEXT"
                       ),
                       "observation_excerpt": next(
                           (o.text[:500] for o in obs if o.id == q.observation_id), "")}
                      for q in qs],
        "findings": [{"id": f.id, "observation_id": f.observation_id,
                      "lane": f.lane, "category": f.category, "title": f.title,
                      "status": f.status, "severity": f.severity, "confidence": f.confidence,
                      "consultant_statement": f.consultant_statement,
                      "consultant_statement_display": consultant_statement_display(f),
                      "model_interpretation": f.model_interpretation,
                      "uncertainty_reasons": f.uncertainty_reasons,
                      "not_supported": f.not_supported,
                      "standard": finding_standard(f),
                      "evidence": [evidence.get(e) for e in f.evidence_ids if e in evidence],
                      "recommended_action": f.recommended_action,
                      "reasoning_trace": f.reasoning_trace or [],
                      "recurrence": f.recurrence or {},
                      "challenge_record": f.challenge_record or {},
                      "ticket": next((_ticket_dict(ticket) for ticket in field_tickets
                                      if f.id in (ticket.source_refs or [])), None),
                      "review_history": f.review_history} for f in fs],
        "field_tickets": [_ticket_dict(ticket) for ticket in field_tickets],
        "actions": [{"id": x.id, "finding_id": x.finding_id, "description": x.description,
                     "owner_role": x.owner_role, "due_date": x.due_date,
                     "verification_method": x.verification_method, "status": x.status,
                     "events": x.events,
                     "verification_capabilities": _verification_capabilities(
                         ticket=_linked_ticket_for_action(db, x), action=x,
                     )} for x in acts],
    }
    db.close()
    return out


@app.get("/api/audits/{audit_id}/budget")
def get_audit_budget(audit_id: str) -> dict:
    db = SessionLocal()
    if db.get(AuditSession, audit_id) is None:
        db.close()
        raise HTTPException(404, "audit not found")
    out = audit_budget(db, audit_id)
    db.close()
    return out


@app.post("/api/audits/{audit_id}/budget/acknowledge")
def acknowledge_audit_budget(audit_id: str, body: BudgetAcknowledgeBody) -> dict:
    """Extend a paused visit visibly and retain the human decision in its log."""
    db = SessionLocal()
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        db.execute(sql_text("BEGIN IMMEDIATE"))
        audit = db.get(AuditSession, audit_id)
    else:
        audit = db.get(AuditSession, audit_id, with_for_update=True)
    if audit is None:
        db.close()
        raise HTTPException(404, "audit not found")
    prior_events = (db.query(AuditLog).filter_by(
        entity_type="audit", entity_id=audit_id, event=BUDGET_EVENT).all())
    if any((event.detail or {}).get("request_id") == body.request_id
           for event in prior_events):
        out = {**audit_budget(db, audit_id), "idempotent": True}
        db.commit()
        db.close()
        return out
    if body.acknowledged_by != audit.consultant_name:
        db.rollback()
        db.close()
        raise HTTPException(403, "only the consultant assigned to this visit can extend its AI budget")
    before = audit_budget(db, audit_id)
    if before["remaining_calls"] > 0:
        db.close()
        raise HTTPException(409, detail={
            "message": "this visit still has analysis capacity", "budget": before,
        })
    if not before["can_acknowledge"]:
        db.close()
        raise HTTPException(409, detail={
            "message": "maximum audited extensions reached; start a separate visit",
            "budget": before,
        })
    db.add(AuditLog(
        id=uid("log"), tenant_id=audit.tenant_id,
        actor=body.acknowledged_by, entity_type="audit", entity_id=audit.id,
        event=BUDGET_EVENT,
        detail={
            "reason": body.reason,
            "used_calls": before["used_calls"],
            "prior_limit": before["limit_calls"],
            "new_limit": before["limit_calls"] + before["extension_calls"],
            "extension_calls": before["extension_calls"],
            "request_id": body.request_id,
        },
    ))
    db.commit()
    out = {**audit_budget(db, audit_id), "idempotent": False}
    db.close()
    return out


@app.post("/api/audits/{audit_id}/observations")
def add_observation(audit_id: str, body: ObservationCreate) -> dict:
    db = SessionLocal()
    a = db.get(AuditSession, audit_id)
    if a is None:
        db.close()
        raise HTTPException(404)
    _ensure_audit_mutable(db, a)
    if body.zone_id:
        zone = db.get(Zone, body.zone_id)
        if zone is None or zone.location_id != a.location_id or zone.tenant_id != a.tenant_id:
            db.close()
            raise HTTPException(422, "zone does not belong to this audit location")
    o = Observation(id=uid("ob"), tenant_id=a.tenant_id, audit_id=audit_id,
                    kind=body.kind, text=body.text, zone_id=body.zone_id,
                    provenance="CONSULTANT_OBSERVATION")
    db.add(o)
    db.commit()
    out = {"id": o.id}
    db.close()
    return out


MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_MEDIA_BYTES = 18 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_MEDIA_TYPES = {
    "AUDIO": {"audio/wav", "audio/mpeg", "audio/mp3", "audio/aiff",
              "audio/aac", "audio/ogg", "audio/flac"},
    "VIDEO": {"video/mp4", "video/webm", "video/mpeg", "video/quicktime"},
}


async def _read_upload_limited(file: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(min(1024 * 1024, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, f"upload exceeds {limit // (1024 * 1024)}MB")
        chunks.append(chunk)
    return b"".join(chunks)


def _media_signature_valid(raw: bytes, mime: str) -> bool:
    if mime == "audio/wav":
        return len(raw) > 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    if mime in {"audio/mpeg", "audio/mp3"}:
        return raw.startswith(b"ID3") or (len(raw) > 1 and raw[0] == 0xFF and raw[1] & 0xE0 == 0xE0)
    if mime == "audio/ogg":
        return raw.startswith(b"OggS")
    if mime == "audio/flac":
        return raw.startswith(b"fLaC")
    if mime == "audio/aiff":
        return len(raw) > 12 and raw[:4] == b"FORM" and raw[8:12] in {b"AIFF", b"AIFC"}
    if mime == "audio/aac":
        return len(raw) > 1 and raw[0] == 0xFF and raw[1] & 0xF0 == 0xF0
    if mime in {"video/mp4", "video/quicktime"}:
        return len(raw) > 12 and raw[4:8] == b"ftyp"
    if mime == "video/webm":
        return raw.startswith(bytes.fromhex("1a45dfa3"))
    if mime == "video/mpeg":
        return raw.startswith(b"\x00\x00\x01")
    return False


@app.post("/api/audits/{audit_id}/photo")
async def add_photo(audit_id: str, file: UploadFile = File(...),
                    zone_id: str | None = Form(None),
                    supports_observation_id: str | None = Form(None),
                    evidence_for_standard_code: str | None = Form(None),
                    privacy_attested: bool = Form(False)) -> dict:
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
    _ensure_audit_mutable(db, a)
    if supports_observation_id and evidence_for_standard_code:
        db.close()
        raise HTTPException(422, "photo can support either an observation or a checklist item, not both")
    target_observation = db.get(Observation, supports_observation_id) if supports_observation_id else None
    if supports_observation_id and (target_observation is None or
                                    target_observation.audit_id != audit_id):
        db.close()
        raise HTTPException(422, "supported observation does not belong to this audit")
    if target_observation is not None:
        if zone_id and target_observation.zone_id and zone_id != target_observation.zone_id:
            db.close()
            raise HTTPException(422, "supporting photo must come from the observation's zone")
        zone_id = target_observation.zone_id or zone_id
    zone = db.get(Zone, zone_id) if zone_id else None
    if zone_id and (zone is None or zone.location_id != a.location_id
                    or zone.tenant_id != a.tenant_id):
        db.close()
        raise HTTPException(422, "zone does not belong to this audit location")
    zone_name = zone.name if zone else ""
    privacy = zone.privacy_level if zone else "NORMAL"
    standard = None
    if evidence_for_standard_code:
        standard = db.query(Standard).filter_by(
            tenant_id=a.tenant_id, code=evidence_for_standard_code, active=True).first()
        if standard is None or zone is None or standard.code not in ZONE_CHECK_CODES.get(zone.name, []):
            db.close()
            raise HTTPException(422, "photo checklist target is not valid for this audit zone")

    if privacy == "HIGH" and not privacy_attested:
        db.close()
        raise HTTPException(
            422, "high-privacy zone requires confirmation that no people or private information are in frame")

    try:
        raw = await _read_upload_limited(file, MAX_PHOTO_BYTES)
    except HTTPException:
        db.close()
        raise
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_IMAGE_TYPES:
        db.close()
        raise HTTPException(415, f"unsupported image type '{mime}'; "
                                 f"allowed: {sorted(ALLOWED_IMAGE_TYPES)}")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("decoded image is too large")
            image.verify()
        # Re-encode before cloud transfer/storage: applies EXIF orientation and
        # strips EXIF/GPS/thumbnail metadata from the canonical evidence file.
        with Image.open(BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image)
            if mime == "image/jpeg" and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            canonical = BytesIO()
            image.save(canonical, format={
                "image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP",
            }[mime])
            raw = canonical.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        db.close()
        raise HTTPException(415, "file contents are not a valid supported image")

    digest = hashlib.sha256(raw).hexdigest()
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[mime]

    desc = None
    vision_error = ""
    try:
        with audit_lock(audit_id):
            desc = get_provider().describe_image(
                image_bytes=raw, mime_type=mime, zone_hint=zone_name,
                privacy_level=privacy,
                evidence_request=(
                    target_observation.text if target_observation is not None
                    else standard.text if standard is not None
                    else ""
                ),
                tenant_id=a.tenant_id, audit_id=audit_id)
    except Exception as exc:
        vision_error = str(exc)
        if not (supports_observation_id or evidence_for_standard_code):
            db.close()
            if isinstance(exc, ModelBudgetExceeded):
                raise
            raise HTTPException(503, f"vision unavailable: {exc}")

    if desc is not None and (not desc.usable_as_evidence or (
            bool(supports_observation_id or evidence_for_standard_code or zone_id)
            and not desc.matches_requested_context)):
        db.close()
        return {"accepted": False,
                "reason": desc.mismatch_reason or desc.unusable_reason,
                "people_visible": desc.people_visible,
                "image_quality_issues": desc.image_quality_issues,
                "note": "No observation was created. An unusable photo is a result, not a failure."}
    if desc is not None and privacy == "HIGH" and desc.people_visible:
        db.close()
        return {"accepted": False,
                "reason": "People are visible in a high-privacy zone; retake without people.",
                "people_visible": True,
                "image_quality_issues": desc.image_quality_issues,
                "note": "The image was analysed transiently but was not stored."}

    stored = config.UPLOADS_DIR / f"{digest}{extension}"
    if not stored.exists():
        stored.write_bytes(raw)

    text = (desc.description if desc is not None else
            f"Photo captured to support: {target_observation.text[:500]}" if target_observation else
            f"Photo captured for checklist item {evidence_for_standard_code}.")
    if desc is not None and desc.visible_facts:
        text += " Visible: " + "; ".join(desc.visible_facts) + "."
    if desc is not None and desc.legible_text:
        text += " Text in frame (transcribed, not interpreted): " + \
                " | ".join(f'"{t}"' for t in desc.legible_text) + "."

    provenance = "MODEL_DESCRIBED_PHOTO" if desc is not None else "PHOTO_CAPTURED_UNDESCRIBED"
    o = Observation(id=uid("ob"), tenant_id=a.tenant_id, audit_id=audit_id,
                    kind="PHOTO_DESCRIPTION", text=text, zone_id=zone_id,
                    provenance=provenance,
                    payload={"image_sha256": digest, "mime": mime, "bytes": len(raw),
                             "zone": zone_name, "zone_privacy_level": privacy,
                             "declined_to_assert": (desc.declined_to_assert if desc else
                                                    ["Automated visual description unavailable"]),
                             "image_quality_issues": desc.image_quality_issues if desc else [],
                             "people_visible": desc.people_visible if desc else None,
                             "vision_model": config.LLM_MODEL if desc else None,
                             "vision_error": vision_error[:500],
                             "semantic_match": (
                                 desc.matches_requested_context if desc is not None else None),
                             "semantic_validation": (
                                 "GEMINI_VALIDATED" if desc is not None
                                 else "MANUAL_REVIEW_REQUIRED"),
                             "supports_observation_id": supports_observation_id,
                             "evidence_for_standard_code": evidence_for_standard_code,
                             "support_only": bool(supports_observation_id or evidence_for_standard_code),
                             "requires_manual_review": bool(
                                 desc is None or (desc and desc.declined_to_assert))})
    ev = EvidenceItem(id=uid("ev"), tenant_id=a.tenant_id, location_id=a.location_id,
                      source_type="PHOTO", collection_method="UPLOAD",
                      provenance=provenance, trust_class="OFFICIAL_OWNED",
                      excerpt=text[:600],
                      payload={"observation_id": o.id, "image_sha256": digest,
                               "supports_observation_id": supports_observation_id,
                               "evidence_for_standard_code": evidence_for_standard_code})
    db.add_all([o, ev])
    db.add(AuditLog(id=uid("log"), tenant_id=a.tenant_id,
                    actor="MODEL" if desc is not None else "SYSTEM",
                    entity_type="observation", entity_id=o.id, event="PHOTO_DESCRIBED",
                    detail={"image_sha256": digest, "zone": zone_name,
                            "declined_to_assert": (desc.declined_to_assert if desc else
                                                   ["Automated visual description unavailable"]),
                            "people_visible": desc.people_visible if desc else None,
                            "supports_observation_id": supports_observation_id,
                            "evidence_for_standard_code": evidence_for_standard_code}))
    routed_ticket_id = None
    if target_observation is not None:
        photo_question = (db.query(ClarificationQuestion).filter_by(
            audit_id=audit_id, observation_id=target_observation.id, status="OPEN")
            .order_by(ClarificationQuestion.created_at.desc()).first())
        if photo_question is not None and photo_question.why_needed.startswith(
                ("PHOTO_REQUIRED:", "PHOTO_RECOMMENDED:",
                 "UNMAPPED_PHOTO_REQUIRED:")):
            photo_question.answer = f"Photo evidence captured: {o.id}"
            photo_question.status = "ANSWERED"
            if photo_question.why_needed.startswith("UNMAPPED_PHOTO_REQUIRED:"):
                category = ("security_presence" if re.search(
                    r"security|guard|officer", target_observation.text, re.I) else "operations")
                dedupe_key = hashlib.sha256(
                    f"UNMAPPED_FIELD_CONCERN|{audit_id}|{target_observation.id}".encode()
                ).hexdigest()
                ticket = db.query(OperationalTicket).filter_by(dedupe_key=dedupe_key).first()
                if ticket is None:
                    now = datetime.now(timezone.utc)
                    assignee = ({
                        "security_presence": "Avery Brooks — Security Supervisor (demo assignee)",
                        "operations": "Taylor Reed — Golf Operations Manager (demo assignee)",
                    }).get(category, "Morgan Patel — Location Manager (demo assignee)")
                    ticket = OperationalTicket(
                        id=uid("ticket"), tenant_id=a.tenant_id,
                        location_id=a.location_id, dedupe_key=dedupe_key,
                        source_kind="UNMAPPED_PHOTO_BACKED_FIELD_CONCERN",
                        source_refs=[target_observation.id, o.id], category=category,
                        title=("Security coverage concern" if category == "security_presence"
                               else "Unmapped field concern"),
                        description=(
                            "The consultant supplied clarification and a linked photo, but no "
                            "controlled standard could be grounded. Routed for operational "
                            "validation without asserting a compliance finding."
                        ),
                        priority="HIGH" if category == "security_presence" else "MEDIUM",
                        assigned_role=assignee, status="PENDING_VALIDATION",
                        validity_status="FIELD_EVIDENCE_ATTACHED",
                        due_date=(now + timedelta(days=2)).date().isoformat(),
                        before_evidence=[{
                            "digest": digest, "mime": mime, "note": text,
                            "actor": a.consultant_name, "at": now.isoformat(),
                            "provenance": provenance, "observation_id": o.id,
                        }],
                        events=[{
                            "at": now.isoformat(), "event": "AUTO_ROUTED_UNMAPPED_FIELD_CONCERN",
                            "by": "SYSTEM",
                            "note": "Photo attached; awaiting operator validation.",
                        }],
                    )
                    db.add(ticket)
                routed_ticket_id = ticket.id
                db.add(AuditLog(
                    id=uid("log"), tenant_id=a.tenant_id, actor="SYSTEM",
                    entity_type="audit", entity_id=audit_id,
                    event="FIELD_CONCERN_ESCALATED",
                    detail={"observation_id": target_observation.id,
                            "photo_observation_id": o.id, "ticket_id": ticket.id,
                            "reason": "No grounded standard after bounded clarification"},
                ))
    db.commit()
    out = {"accepted": True, "observation_id": o.id, "text": text,
           "image_sha256": digest,
           "declined_to_assert": desc.declined_to_assert if desc else
                                 ["Automated visual description unavailable"],
           "image_quality_issues": desc.image_quality_issues if desc else [],
           "people_visible": desc.people_visible if desc else None,
           "zone_privacy_level": privacy, "provenance": provenance,
           "supports_observation_id": supports_observation_id,
           "evidence_for_standard_code": evidence_for_standard_code,
           "requires_manual_review": bool(
               desc is None or (desc and desc.declined_to_assert)),
           "ticket_id": routed_ticket_id}
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


@app.get("/api/media/{digest}")
def get_media(digest: str):
    """POC media review route. Production requires authenticated tenant scope."""
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise HTTPException(400, "bad digest")
    for path in config.UPLOADS_DIR.glob(f"{digest}.*"):
        return FileResponse(path)
    raise HTTPException(404)


@app.post("/api/audits/{audit_id}/media")
async def add_media(audit_id: str, file: UploadFile = File(...),
                    media_kind: Literal["AUDIO", "VIDEO"] = Form(...),
                    zone_id: str | None = Form(None),
                    standard_code: str | None = Form(None),
                    privacy_attested: bool = Form(False)) -> dict:
    """Audio/video -> neutral observation; never a direct finding."""
    db = SessionLocal()
    audit = db.get(AuditSession, audit_id)
    if audit is None:
        db.close()
        raise HTTPException(404, "audit not found")
    _ensure_audit_mutable(db, audit)
    zone = db.get(Zone, zone_id) if zone_id else None
    if zone_id and (zone is None or zone.location_id != audit.location_id
                    or zone.tenant_id != audit.tenant_id):
        db.close()
        raise HTTPException(422, "zone does not belong to this audit location")
    privacy = zone.privacy_level if zone else "NORMAL"
    if media_kind == "VIDEO" and privacy == "HIGH" and not privacy_attested:
        db.close()
        raise HTTPException(
            422, "high-privacy video requires confirmation that no people or private information are in frame")

    standard = None
    if standard_code:
        standard = db.query(Standard).filter_by(
            tenant_id=audit.tenant_id, code=standard_code, active=True).first()
        if (standard is None or zone is None
                or standard.code not in ZONE_CHECK_CODES.get(zone.name, [])):
            db.close()
            raise HTTPException(422, "media checklist target is not valid for this audit zone")

    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MEDIA_TYPES[media_kind]:
        db.close()
        raise HTTPException(
            415, f"unsupported {media_kind.lower()} type; allowed: {sorted(ALLOWED_MEDIA_TYPES[media_kind])}")
    try:
        raw = await _read_upload_limited(file, MAX_MEDIA_BYTES)
    except HTTPException:
        db.close()
        raise
    if not raw or not _media_signature_valid(raw, mime):
        db.close()
        raise HTTPException(415, "file contents do not match the declared media type")

    try:
        with audit_lock(audit_id):
            desc = get_provider().describe_media(
                media_bytes=raw, mime_type=mime, media_kind=media_kind,
                zone_hint=(zone.name if zone else ""), privacy_level=privacy,
                standard_hint=(f"{standard.code}: {standard.text}" if standard else ""),
                tenant_id=audit.tenant_id, audit_id=audit_id,
            )
    except ModelBudgetExceeded:
        db.close()
        raise
    except Exception:
        db.close()
        raise HTTPException(
            503, "multimodal analysis unavailable; media was not stored and no observation was created")

    if not desc.usable_as_evidence or not desc.matches_requested_context:
        db.close()
        return {"accepted": False,
                "reason": desc.mismatch_reason or desc.unusable_reason,
                "quality_issues": desc.quality_issues,
                "note": "No observation was created from unusable media."}
    if media_kind == "VIDEO" and privacy == "HIGH" and desc.people_visible:
        db.close()
        return {"accepted": False,
                "reason": "People are visible in a high-privacy zone; retake without people.",
                "people_visible": True, "quality_issues": desc.quality_issues,
                "note": "The clip was analysed transiently but was not stored."}

    digest = hashlib.sha256(raw).hexdigest()
    extension = {
        "audio/wav": ".wav", "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
        "audio/aiff": ".aiff", "audio/aac": ".aac", "audio/ogg": ".ogg",
        "audio/flac": ".flac", "video/mp4": ".mp4", "video/webm": ".webm",
        "video/mpeg": ".mpeg", "video/quicktime": ".mov",
    }[mime]
    stored = config.UPLOADS_DIR / f"{digest}{extension}"
    if not stored.exists():
        stored.write_bytes(raw)

    if media_kind == "AUDIO":
        text = f"Consultant voice note (model transcript): {desc.transcript or desc.description}"
        kind = "VOICE_TRANSCRIPT"
        provenance = "MODEL_TRANSCRIBED_AUDIO"
        source_type = "AUDIO"
        trust_class = "CONSULTANT_ATTESTATION"
    else:
        text = f"Short video description: {desc.description}"
        if desc.observable_facts:
            text += " Visible/audible facts: " + "; ".join(desc.observable_facts) + "."
        kind = "VIDEO_DESCRIPTION"
        provenance = "MODEL_DESCRIBED_VIDEO"
        source_type = "VIDEO"
        trust_class = "OFFICIAL_OWNED"

    observation = Observation(
        id=uid("ob"), tenant_id=audit.tenant_id, audit_id=audit_id,
        kind=kind, text=text, zone_id=zone_id, provenance=provenance,
        payload={
            "media_sha256": digest, "mime": mime, "bytes": len(raw),
            "media_kind": media_kind, "zone": zone.name if zone else "",
            "zone_privacy_level": privacy, "standard_code": standard_code,
            "standard_authoritative": False if standard else None,
            "transcript": desc.transcript, "observable_facts": desc.observable_facts,
            "timecoded_facts": desc.timecoded_facts,
            "declined_to_assert": desc.declined_to_assert,
            "quality_issues": desc.quality_issues,
            "people_visible": desc.people_visible, "model": config.LLM_MODEL,
            "semantic_match": desc.matches_requested_context,
            "semantic_validation": "GEMINI_VALIDATED",
            "verification_state": (
                "CONSULTANT_REPORTED" if media_kind == "AUDIO" else "MEDIA_CAPTURED"),
            "awaiting_confirmation": media_kind == "AUDIO",
        },
    )
    evidence = EvidenceItem(
        id=uid("ev"), tenant_id=audit.tenant_id, location_id=audit.location_id,
        source_type=source_type, collection_method="UPLOAD",
        provenance=provenance, trust_class=trust_class, excerpt=text[:600],
        payload={"observation_id": observation.id, "media_sha256": digest},
    )
    db.add_all([observation, evidence])
    db.add(AuditLog(
        id=uid("log"), tenant_id=audit.tenant_id, actor="MODEL",
        entity_type="observation", entity_id=observation.id,
        event=f"{media_kind}_DESCRIBED",
        detail={"media_sha256": digest, "zone": zone.name if zone else "",
                "declined_to_assert": desc.declined_to_assert},
    ))
    db.commit()
    out = {
        "accepted": True, "observation_id": observation.id, "text": text,
        "media_sha256": digest, "media_kind": media_kind,
        "provenance": provenance, "verification_state": observation.payload["verification_state"],
        "awaiting_confirmation": observation.payload["awaiting_confirmation"],
        "transcript": desc.transcript, "observable_facts": desc.observable_facts,
        "timecoded_facts": desc.timecoded_facts,
        "declined_to_assert": desc.declined_to_assert,
        "quality_issues": desc.quality_issues, "people_visible": desc.people_visible,
    }
    db.close()
    return out


@app.post("/api/audits/{audit_id}/checklist")
def submit_checklist(audit_id: str, body: ChecklistSubmit) -> dict:
    with audit_lock(audit_id):
        return _submit_checklist_unlocked(audit_id, body)


def _submit_checklist_unlocked(audit_id: str, body: ChecklistSubmit) -> dict:
    db = SessionLocal()
    a = db.get(AuditSession, audit_id)
    if a is None:
        db.close()
        raise HTTPException(404)
    _ensure_audit_mutable(db, a)
    responses = [r.model_dump() for r in body.responses]
    standards_by_code: dict[str, Standard] = {}
    for r in responses:
        zone = None
        if r.get("zone_id"):
            zone = db.get(Zone, r["zone_id"])
            if zone is None or zone.location_id != a.location_id or zone.tenant_id != a.tenant_id:
                db.close()
                raise HTTPException(422, "checklist zone does not belong to this audit location")
        standard = db.query(Standard).filter_by(
            tenant_id=a.tenant_id, code=r["standard_code"], active=True).first()
        if standard is None:
            db.close()
            raise HTTPException(422, "checklist standard does not belong to this audit tenant")
        standards_by_code[standard.code] = standard
        if zone is not None and standard.code not in ZONE_CHECK_CODES.get(zone.name, []):
            db.close()
            raise HTTPException(
                422,
                f"standard {standard.code} is not applicable to checklist zone {zone.name}",
            )
        # The client selects a server-owned standard code; it cannot substitute
        # friendlier or stricter requirement text.
        r["item"] = standard.text
        r["source_label"] = standard.source_label
        r["standard_metadata"] = standard_metadata(standard.code)
        authority_type = (r["standard_metadata"].get("authority_type") or "")
        if "CONDITIONAL" in authority_type and r["response"] in {
                "PASS", "NOT_APPLICABLE"} and len((r.get("detail") or "").strip()) < 5:
            db.close()
            raise HTTPException(
                422,
                f"conditional check {standard.code} requires the verified condition or applicability reason",
            )
        evidence_ids = list(dict.fromkeys(r.get("evidence_observation_ids") or []))
        r["evidence_observation_ids"] = evidence_ids
        photo_policy = issue_photo_policy(
            standard.code,
            category=standard.category,
            severity=standard.severity_default,
        )
        r["photo_policy"] = photo_policy
        if r["response"] == "FAIL" and not evidence_ids:
            if photo_policy["level"] == "REQUIRED":
                db.close()
                raise HTTPException(
                    422, f"issue {standard.code} requires an explicitly linked photo")
            if r.get("photo_decision") != "CONTINUE_WITHOUT_PHOTO":
                db.close()
                raise HTTPException(
                    422,
                    f"AI recommends a photo for issue {standard.code}; attach one or "
                    "explicitly continue without a photo",
                )
        if evidence_ids:
            evidence_rows = db.query(Observation).filter(
                Observation.id.in_(evidence_ids), Observation.audit_id == audit_id).all()
            if len(evidence_rows) != len(evidence_ids) or any(
                    o.kind != "PHOTO_DESCRIPTION"
                    for o in evidence_rows):
                db.close()
                raise HTTPException(422, "checklist issue evidence must be a photo from this audit")
            if zone is not None and any(o.zone_id != zone.id for o in evidence_rows):
                db.close()
                raise HTTPException(
                    422, "checklist evidence must come from the same inspection zone")
            for evidence_row in evidence_rows:
                payload = evidence_row.payload or {}
                if payload.get("semantic_match") is False:
                    db.close()
                    raise HTTPException(
                        422, "checklist evidence was assessed as unrelated to this issue")
                direct_code = payload.get("evidence_for_standard_code")
                supported_observation_id = payload.get("supports_observation_id")
                linked_finding = (db.query(Finding).filter_by(
                    audit_id=audit_id,
                    observation_id=supported_observation_id,
                    standard_id=standard.id,
                ).first() if supported_observation_id else None)
                if direct_code != standard.code and linked_finding is None:
                    db.close()
                    raise HTTPException(
                        422,
                        f"photo is not linked to checklist issue {standard.code}; "
                        "capture or select evidence for this exact issue",
                    )
            r["photo_decision"] = "ATTACHED"
        r["verification_state"] = (
            "PHOTO_ATTACHED_PENDING_REVIEW" if evidence_ids else
            "CONSULTANT_REPORTED_PHOTO_RECOMMENDED"
            if r["response"] == "FAIL" else "CONSULTANT_REPORTED")
    # A field visit submits one area at a time. Preserve prior areas and replace
    # only the same zone/standard answer; overwriting the JSON list here made a
    # multi-zone audit appear complete while retaining only its final screen.
    prior_responses = {
        (row.get("zone_id"), row.get("standard_code")): row
        for row in (a.checklist_responses or [])
    }
    for row in responses:
        prior = prior_responses.get((row.get("zone_id"), row.get("standard_code"))) or {}
        conflict = prior.get("reconciliation_conflict")
        if conflict:
            row["conflict_resolution"] = (
                "CONSULTANT_CONFIRMED_ISSUE" if row["response"] == "FAIL"
                else "CONSULTANT_RETAINED_RESPONSE"
            )
            row["contradicting_finding_id"] = conflict.get("finding_id")
            row["review_required"] = True
        originating_finding_id = prior.get("finding_id") if prior.get(
            "auto_reconciled") else None
        originating_finding = db.get(Finding, originating_finding_id) \
            if originating_finding_id else None
        response_standard = standards_by_code[row["standard_code"]]
        if (originating_finding is not None
                and originating_finding.audit_id == audit_id
                and (originating_finding.standard_id == response_standard.id)):
            # Saving the rest of a zone often resubmits a server-generated
            # checklist row.  The free-form observation already owns the
            # finding and ticket; treating this representation as a brand-new
            # failed checklist observation duplicates the same incident.
            row["finding_id"] = originating_finding.id
            row["originating_finding_id"] = originating_finding.id
            row["review_required"] = True
            row["conflict_resolution"] = (
                "CONSULTANT_CONFIRMED_ISSUE" if row["response"] == "FAIL"
                else "CONSULTANT_RETAINED_RESPONSE"
            )
    merged_responses = dict(prior_responses)
    for row in responses:
        merged_responses[(row.get("zone_id"), row.get("standard_code"))] = row
    a.checklist_responses = list(merged_responses.values())
    # failing checklist items become observations so they flow through analysis
    created, updated, retracted = [], [], []
    existing_observations = {
        (o.payload or {}).get("checklist_key"): o for o in
        db.query(Observation).filter_by(audit_id=audit_id, kind="CHECKLIST").all()
    }
    for r in responses:
        key_material = f"{r['standard_code']}|{r.get('zone_id') or ''}"
        checklist_key = hashlib.sha256(key_material.encode()).hexdigest()[:24]
        existing = existing_observations.get(checklist_key)
        downstream = (db.query(Finding).filter_by(observation_id=existing.id).first()
                      if existing is not None else None)
        if downstream is not None:
            prior_payload = existing.payload or {}
            prior_detail = prior_payload.get("detail")
            if prior_detail is None:
                prior_detail = existing.text.rsplit(" — ", 1)[-1]
            unchanged = (
                r["response"] == "FAIL" and
                (r.get("detail") or "").strip() == str(prior_detail).strip() and
                set(r.get("evidence_observation_ids") or []) ==
                set(prior_payload.get("evidence_observation_ids") or [])
            )
            if not unchanged:
                db.close()
                raise HTTPException(
                    409,
                    "this checklist issue already has a review packet; correct or retract it in review",
                )
            continue
        if r.get("contradicting_finding_id") or r.get("originating_finding_id"):
            # The consultant explicitly resolved a system-raised conflict.
            # The originating free-form observation already owns the finding,
            # so do not manufacture a duplicate checklist observation.
            continue
        if r["response"] == "FAIL":
            payload = {"checklist_key": checklist_key, "response": r["response"],
                       "standard_code": r["standard_code"],
                       "detail": (r.get("detail") or "").strip(),
                       "standard_source": r["source_label"],
                       "standard_metadata": r.get("standard_metadata") or {},
                       "authoritative": False,
                       "evidence_observation_ids": r.get("evidence_observation_ids") or [],
                       "photo_decision": r.get("photo_decision"),
                       "photo_policy": r.get("photo_policy") or {},
                       "verification_state": r["verification_state"]}
            text = f"Checklist item failed: {r['item']} — {r.get('detail')}"
            if existing is None:
                existing = Observation(
                    id=uid("ob"), tenant_id=a.tenant_id, audit_id=audit_id,
                    kind="CHECKLIST", zone_id=r.get("zone_id"),
                    provenance="CONSULTANT_OBSERVATION", text=text, payload=payload)
                db.add(existing)
                created.append(existing.id)
                existing_observations[checklist_key] = existing
            else:
                existing.text = text
                existing.payload = payload
                updated.append(existing.id)
        elif existing is not None:
            retracted.append(existing.id)
            db.delete(existing)
    db.commit()
    db.close()
    return {"observations_created": created, "observations_updated": updated,
            "observations_retracted": retracted, "responses_saved": len(responses),
            "authority": "MIXED_SOURCED_GUIDANCE"}


@app.post("/api/audits/{audit_id}/analyze")
def analyze(audit_id: str) -> dict:
    db = SessionLocal()
    audit = db.get(AuditSession, audit_id)
    if audit is None:
        db.close()
        raise HTTPException(404, "audit not found")
    _ensure_audit_mutable(db, audit)
    db.close()
    try:
        return analyze_audit(audit_id)
    except RuntimeError as e:
        raise HTTPException(429, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/audits/{audit_id}/submit")
def submit_audit(audit_id: str, body: AuditSubmitBody) -> dict:
    """Finish a visit only when its required evidence packet is complete.

    Analysis may prepare candidate findings while collection is still underway;
    this endpoint is the explicit consultant handoff. It refuses optimistic
    coverage, unresolved observations, and empty "no issue" submissions.
    """
    db = SessionLocal()
    audit = db.get(AuditSession, audit_id)
    if audit is None:
        db.close()
        raise HTTPException(404, "audit not found")

    prior_submission = (
        db.query(AuditLog)
        .filter_by(entity_type="audit", entity_id=audit_id, event="SUBMITTED")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    if prior_submission is not None:
        out = {
            "id": audit.id, "status": audit.status, "idempotent": True,
            "submission": prior_submission.detail or {},
        }
        db.close()
        return out

    active_codes = {
        row.code for row in db.query(Standard).filter_by(
            tenant_id=audit.tenant_id, active=True).all()
    }
    required_zones = db.query(Zone).filter_by(
        location_id=audit.location_id, tenant_id=audit.tenant_id, required=True
    ).all()
    answered = {
        (row.get("zone_id"), row.get("standard_code")): row
        for row in (audit.checklist_responses or [])
    }
    conflicts = [row for row in answered.values()
                 if row.get("reconciliation_conflict")]
    if conflicts:
        db.close()
        raise HTTPException(409, detail={
            "message": "checklist conflicts must be confirmed by the consultant",
            "conflicts": [{
                "zone_id": row.get("zone_id"),
                "standard_code": row.get("standard_code"),
                "preserved_response": row.get("response"),
            } for row in conflicts],
        })
    missing_checks: list[dict] = []
    required_check_count = 0
    for zone in required_zones:
        codes = [code for code in ZONE_CHECK_CODES.get(zone.name, [])
                 if code in active_codes]
        if not codes:
            missing_checks.append({
                "zone_id": zone.id, "zone": zone.name,
                "reason": "NO_ACTIVE_CHECKS_CONFIGURED",
            })
            continue
        required_check_count += len(codes)
        for code in codes:
            if (zone.id, code) not in answered:
                missing_checks.append({
                    "zone_id": zone.id, "zone": zone.name,
                    "standard_code": code, "reason": "UNANSWERED",
                })
    if missing_checks:
        db.close()
        raise HTTPException(409, detail={
            "message": "required field-guide checks are incomplete",
            "missing_checks": missing_checks,
        })

    open_questions = db.query(ClarificationQuestion).filter_by(
        audit_id=audit_id, status="OPEN").all()
    if open_questions:
        ids = [row.id for row in open_questions]
        db.close()
        raise HTTPException(409, detail={
            "message": "open clarification questions must be resolved before submission",
            "question_ids": ids,
        })

    observations = db.query(Observation).filter_by(audit_id=audit_id).all()
    unconfirmed = [row.id for row in observations
                   if (row.payload or {}).get("awaiting_confirmation")]
    if unconfirmed:
        db.close()
        raise HTTPException(409, detail={
            "message": "voice transcripts must be confirmed before submission",
            "observation_ids": unconfirmed,
        })

    findings = db.query(Finding).filter_by(audit_id=audit_id).all()
    reviewable_findings = [row for row in findings
                           if row.status == "READY_FOR_REVIEW"]
    settled_observation_ids = {
        row.observation_id for row in findings if row.observation_id
    }
    no_issue_observation_ids: set[str] = set()
    for log in db.query(AuditLog).filter_by(
            entity_type="audit", entity_id=audit_id, event="ANALYZE").all():
        no_issue_observation_ids.update((log.detail or {}).get("no_issue", []))
    settled_observation_ids.update(no_issue_observation_ids)
    unsettled = [row.id for row in observations
                 if row.id not in settled_observation_ids
                 and not (row.payload or {}).get("support_only")]
    if unsettled:
        db.close()
        raise HTTPException(409, detail={
            "message": "every captured observation must be analysed before submission",
            "observation_ids": unsettled,
        })

    failed_checks = [row for row in (audit.checklist_responses or [])
                     if row.get("response") == "FAIL"]
    if not reviewable_findings:
        if not body.no_issue_attestation:
            db.close()
            raise HTTPException(409, detail={
                "message": (
                    "no candidate packet exists; explicitly attest that the completed "
                    "visit found no issues"
                ),
            })
        if failed_checks:
            db.close()
            raise HTTPException(409, detail={
                "message": "a no-issue submission cannot contain failed checklist responses",
                "failed_standard_codes": sorted({row.get("standard_code")
                                                  for row in failed_checks}),
            })

    audit.status = "READY_FOR_REVIEW" if reviewable_findings else "SUBMITTED"
    detail = {
        "submitted_by": body.submitted_by,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "required_zones": len(required_zones),
        "required_checks": required_check_count,
        "candidate_findings": len(reviewable_findings),
        "no_issue_attestation": bool(body.no_issue_attestation),
        "no_issue_observations": len(no_issue_observation_ids),
    }
    db.add(AuditLog(
        id=uid("log"), tenant_id=audit.tenant_id, actor=body.submitted_by,
        entity_type="audit", entity_id=audit.id, event="SUBMITTED", detail=detail,
    ))
    db.commit()
    out = {"id": audit.id, "status": audit.status,
           "idempotent": False, "submission": detail}
    db.close()
    return out


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
    db = SessionLocal()
    question = db.get(ClarificationQuestion, question_id)
    if question is None:
        db.close()
        raise HTTPException(404, "question not found")
    audit = db.get(AuditSession, question.audit_id)
    _ensure_audit_mutable(db, audit)
    db.close()
    try:
        return answer_clarification(question_id, body.answer)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 409, str(e))


@app.post("/api/findings/{finding_id}/review")
def review(finding_id: str, body: ReviewBody) -> dict:
    try:
        return review_finding(finding_id, action=body.action, reviewer=body.reviewer,
                              edits=body.edits, reason=body.reason)
    except ValueError as e:
        status = 404 if "not found" in str(e) else 409
        raise HTTPException(status, str(e))


@app.post("/api/findings/{finding_id}/challenge")
def challenge_finding(finding_id: str, body: ChallengeBody) -> dict:
    try:
        return challenge_existing_finding(finding_id, body.reviewer)
    except ValueError as e:
        raise HTTPException(404, str(e))


class VerifyBody(BaseModel):
    evidence_description: str = Field(min_length=3, max_length=5000)
    verified_by: str = Field(default="Manager", min_length=2, max_length=120)

    @field_validator("evidence_description")
    @classmethod
    def non_blank_evidence(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("verification evidence must describe what was checked")
        return value


class TicketValidityBody(BaseModel):
    verdict: Literal["VALIDATED_ON_SITE", "NOT_SUBSTANTIATED"]
    actor: str = Field(default="Location Manager", min_length=2, max_length=120)
    reason: str = Field(min_length=3, max_length=3000)


class TicketResolutionBody(BaseModel):
    actor: str = Field(default="Assigned Staff", min_length=2, max_length=120)
    resolution_note: str = Field(min_length=3, max_length=3000)


class TicketVerificationBody(BaseModel):
    actor: str = Field(default="Operations Manager", min_length=2, max_length=120)
    verification_note: str = Field(min_length=3, max_length=3000)


class TaxonomyDecisionBody(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    reviewer: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=3, max_length=3000)


@app.post("/api/actions/{action_id}/evidence")
async def add_action_evidence(action_id: str, file: UploadFile = File(...),
                              actor: str = Form("Location Manager"),
                              note: str = Form("Corrected condition shown")) -> dict:
    """Attach a real after-photo before corrective action verification."""
    db = SessionLocal()
    action = db.get(Action, action_id)
    if action is None:
        db.close()
        raise HTTPException(404, "action not found")
    if action.status == "VERIFIED":
        db.close()
        raise HTTPException(409, "verified action cannot accept new evidence")
    linked_ticket = _linked_ticket_for_action(db, action)
    if linked_ticket is not None and not (
            linked_ticket.status == "OPEN"
            and linked_ticket.validity_status in {
                "VALIDATED_ON_SITE", "VALIDATED_BY_FINDING_REVIEW",
            }):
        db.close()
        raise HTTPException(
            409, "linked ticket must be validated and open before after evidence is added")
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_IMAGE_TYPES:
        db.close()
        raise HTTPException(415, "verification evidence must be JPEG, PNG or WebP")
    try:
        raw = await _read_upload_limited(file, MAX_PHOTO_BYTES)
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("decoded image is too large")
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image)
            if mime == "image/jpeg" and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            canonical = BytesIO()
            image.save(canonical, format={
                "image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP",
            }[mime])
            raw = canonical.getvalue()
    except HTTPException:
        db.close()
        raise
    except (UnidentifiedImageError, OSError, ValueError):
        db.close()
        raise HTTPException(415, "file contents are not a valid supported image")
    digest = hashlib.sha256(raw).hexdigest()
    if linked_ticket is not None:
        if digest in {row.get("digest") for row in (linked_ticket.before_evidence or [])}:
            db.close()
            raise HTTPException(409, "after evidence must differ from before evidence")
        if digest in {row.get("digest") for row in (linked_ticket.after_evidence or [])}:
            db.close()
            raise HTTPException(409, "this image is already attached as after evidence")
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[mime]
    stored = config.UPLOADS_DIR / f"{digest}{extension}"
    if not stored.exists():
        stored.write_bytes(raw)
    event = {"at": datetime.now(timezone.utc).isoformat(), "event": "AFTER_EVIDENCE_UPLOADED",
             "by": actor, "note": note[:1000], "image_sha256": digest,
             "provenance": "STAFF_UPLOADED_PHOTO"}
    action.events = [*action.events, event]
    action.status = "COMPLETE_UNVERIFIED"
    if linked_ticket is not None:
        ticket_evidence = {
            "digest": digest, "mime": mime, "note": note[:1000],
            "actor": actor, "at": event["at"],
            "provenance": "STAFF_UPLOADED_PHOTO",
            "action_id": action.id,
        }
        linked_ticket.after_evidence = [
            *(linked_ticket.after_evidence or []), ticket_evidence,
        ]
        linked_ticket.status = "RESOLVED_PENDING_VERIFICATION"
        linked_ticket.events = [*(linked_ticket.events or []), {
            "at": event["at"], "event": "ACTION_RESOLUTION_SUBMITTED",
            "by": actor, "note": note[:1000], "digest": digest,
            "action_id": action.id,
        }]
    db.add(AuditLog(id=uid("log"), tenant_id=action.tenant_id, actor=actor,
                    entity_type="action", entity_id=action.id,
                    event="AFTER_EVIDENCE_UPLOADED", detail=event))
    db.commit()
    out = {"id": action.id, "status": action.status,
           "image_sha256": digest, "provenance": "STAFF_UPLOADED_PHOTO",
           "ticket_id": linked_ticket.id if linked_ticket else None,
           "ticket_status": linked_ticket.status if linked_ticket else None,
           "verification_capabilities": _verification_capabilities(
               ticket=linked_ticket, action=action),
           }
    db.close()
    return out


@app.post("/api/actions/{action_id}/verify")
def verify_action(action_id: str, body: VerifyBody) -> dict:
    db = SessionLocal()
    x = db.get(Action, action_id)
    if x is None:
        db.close()
        raise HTTPException(404)
    linked_ticket = _linked_ticket_for_action(db, x)
    if x.status == "VERIFIED" and (
            linked_ticket is None or linked_ticket.status == "CLOSED_VERIFIED"):
        out = {"id": x.id, "status": x.status, "idempotent": True,
               "ticket_id": linked_ticket.id if linked_ticket else None}
        db.close()
        return out
    evidence = [event for event in x.events
                if event.get("event") == "AFTER_EVIDENCE_UPLOADED"]
    if not evidence:
        db.close()
        raise HTTPException(409, "a real after-photo is required before verification")
    if linked_ticket is not None and linked_ticket.status != "RESOLVED_PENDING_VERIFICATION":
        db.close()
        raise HTTPException(
            409, "linked ticket resolution must be submitted before action verification")
    capabilities = _verification_capabilities(ticket=linked_ticket, action=x)
    verifier = body.verified_by.strip().casefold()
    excluded = {actor.casefold() for actor in capabilities["excluded_actors"]}
    if verifier in excluded:
        db.close()
        raise HTTPException(
            409, "independent verification requires a different actor from review and resolution")
    verified_at = datetime.now(timezone.utc)
    x.status = "VERIFIED"
    x.events = [*x.events, {"at": verified_at.isoformat(),
                            "event": "VERIFIED", "by": body.verified_by,
                            "evidence": body.evidence_description,
                            "image_sha256": evidence[-1]["image_sha256"],
                            "provenance": "STAFF_UPLOADED_PHOTO"}]
    if linked_ticket is not None:
        linked_ticket.status = "CLOSED_VERIFIED"
        linked_ticket.resolved_at = verified_at
        linked_ticket.events = [*(linked_ticket.events or []), {
            "at": verified_at.isoformat(), "event": "CLOSED_VERIFIED",
            "by": body.verified_by, "note": body.evidence_description,
            "digest": evidence[-1]["image_sha256"], "action_id": x.id,
        }]
    db.commit()
    out = {"id": x.id, "status": x.status,
           "ticket_id": linked_ticket.id if linked_ticket else None,
           "ticket_status": linked_ticket.status if linked_ticket else None}
    db.close()
    return out


# ---------------- customer-signal resolution loop ----------------

_TICKET_OWNER = {
    "cleanliness": "Facilities Manager",
    "safety": "Safety Manager",
    "course_condition": "Director of Golf",
    "operations": "Golf Operations Manager",
}

_TAXONOMY_GAPS = {
    "On-course drinking water availability": (
        "guest_hydration_availability",
        "Guest hydration availability",
        "Separating hydration access from generic operations makes heat-readiness and guest-service checks measurable.",
    ),
    "Service responsiveness and value": (
        "service_recovery_responsiveness",
        "Service recovery responsiveness",
        "A dedicated parameter can connect response-time evidence to perceived value without treating sentiment as proof.",
    ),
    "Golf cart reliability / GPS controls": (
        "cart_technology_reliability",
        "Cart technology reliability",
        "Fleet faults and GPS restrictions need a measurable check distinct from general operations.",
    ),
    "Temporary greens and pre-arrival disclosure": (
        "course_condition_disclosure",
        "Course-condition disclosure",
        "Pre-arrival disclosure should be checked independently from the physical course condition itself.",
    ),
}


def _ticket_dict(ticket: OperationalTicket) -> dict:
    return {
        "id": ticket.id, "tenant_id": ticket.tenant_id,
        "location_id": ticket.location_id, "source_kind": ticket.source_kind,
        "source_refs": ticket.source_refs, "category": ticket.category,
        "title": ticket.title, "description": ticket.description,
        "priority": ticket.priority, "assigned_role": ticket.assigned_role,
        "status": ticket.status, "validity_status": ticket.validity_status,
        "due_date": ticket.due_date, "before_evidence": ticket.before_evidence,
        "after_evidence": ticket.after_evidence,
        "external_reply": ticket.external_reply, "events": ticket.events,
        "verification_capabilities": _verification_capabilities(ticket=ticket),
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
    }


@app.post("/api/locations/{location_id}/tickets/sync")
def sync_signal_tickets(location_id: str) -> dict:
    """Create one idempotent triage ticket per recurring negative theme.

    A theme opens a question for the operator; it does not establish that the
    review was valid or create a compliance finding. On-site validation is a
    required, explicit state transition.
    """
    db = SessionLocal()
    loc = db.get(Location, location_id)
    db.close()
    if loc is None:
        raise HTTPException(404, "location not found")
    signal = summarise_themes(location_id, loc.tenant_id)
    captured = signal["sample"].get("captured_at") or "live-sample"
    created: list[str] = []
    existing: list[str] = []
    db = SessionLocal()
    for theme in signal["themes"].get("themes", []):
        if int(theme.get("mention_count") or 0) < 2:
            continue
        links = theme.get("linked_categories") or []
        category = (links[0].get("category") if links else "operations")
        dedupe_key = hashlib.sha256(
            f"{location_id}|{captured}|{theme['theme']}".encode()).hexdigest()
        prior = db.query(OperationalTicket).filter_by(dedupe_key=dedupe_key).first()
        if prior:
            existing.append(prior.id)
            continue
        mentions = int(theme["mention_count"])
        priority = "HIGH" if mentions >= 3 or category == "safety" else "MEDIUM"
        due_days = 2 if priority == "HIGH" else 5
        now = datetime.now(timezone.utc)
        ticket = OperationalTicket(
            id=uid("ticket"), tenant_id=loc.tenant_id, location_id=location_id,
            dedupe_key=dedupe_key, source_refs=theme.get("review_ids") or [],
            category=category, title=theme["theme"],
            description=(f"Recurring customer signal across {mentions} recent low-rating "
                         "written reviews. Validate on site before treating it as true."),
            priority=priority,
            assigned_role=_TICKET_OWNER.get(category, "Location Manager"),
            due_date=(now + timedelta(days=due_days)).date().isoformat(),
            events=[{"at": now.isoformat(), "event": "AUTO_TRIAGED",
                     "by": "SYSTEM", "provenance": signal["sample"]["provenance"],
                     "note": "Customer signal opened triage; no violation asserted."}],
        )
        db.add(ticket)
        created.append(ticket.id)
    db.commit()
    db.close()
    return {"created": created, "existing": existing,
            "source_provenance": signal["sample"]["provenance"]}


@app.get("/api/locations/{location_id}/tickets")
def list_signal_tickets(location_id: str) -> dict:
    db = SessionLocal()
    loc = db.get(Location, location_id)
    if loc is None:
        db.close()
        raise HTTPException(404, "location not found")
    tickets = (db.query(OperationalTicket).filter_by(location_id=location_id)
                 .order_by(OperationalTicket.created_at.desc()).all())
    out = {"location_id": location_id, "tickets": [_ticket_dict(t) for t in tickets]}
    db.close()
    return out


@app.post("/api/tickets/{ticket_id}/evidence")
async def add_ticket_evidence(ticket_id: str, stage: str = Form(...),
                              note: str = Form(...), actor: str = Form("Assigned Staff"),
                              file: UploadFile = File(...)) -> dict:
    stage = stage.upper().strip()
    if stage not in {"BEFORE", "AFTER"}:
        raise HTTPException(422, "stage must be BEFORE or AFTER")
    if len(note.strip()) < 3 or len(actor.strip()) < 2:
        raise HTTPException(422, "evidence requires an actor and descriptive note")
    db = SessionLocal()
    ticket = db.get(OperationalTicket, ticket_id)
    if ticket is None:
        db.close()
        raise HTTPException(404, "ticket not found")
    if stage == "BEFORE" and ticket.status != "PENDING_VALIDATION":
        db.close()
        raise HTTPException(409, "before evidence is accepted only while validation is pending")
    if stage == "AFTER" and not (
            ticket.status == "OPEN" and ticket.validity_status in {
                "VALIDATED_ON_SITE", "VALIDATED_BY_FINDING_REVIEW"}):
        db.close()
        raise HTTPException(
            409, "after evidence is accepted only after the concern has been validated")
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_IMAGE_TYPES:
        db.close()
        raise HTTPException(415, "unsupported evidence image type")
    try:
        raw = await _read_upload_limited(file, MAX_PHOTO_BYTES)
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("decoded image is too large")
            image.verify()
        # Use the same canonical evidence pipeline as field/action photos so
        # ticket uploads cannot retain EXIF, GPS or embedded thumbnails.
        with Image.open(BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image)
            if mime == "image/jpeg" and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            canonical = BytesIO()
            image.save(canonical, format={
                "image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP",
            }[mime])
            raw = canonical.getvalue()
    except HTTPException:
        db.close()
        raise
    except (UnidentifiedImageError, OSError, ValueError):
        db.close()
        raise HTTPException(415, "file contents are not a valid supported image")
    digest = hashlib.sha256(raw).hexdigest()
    if stage == "AFTER" and digest in {
            row.get("digest") for row in (ticket.before_evidence or [])}:
        db.close()
        raise HTTPException(409, "after evidence must be a different image from before evidence")
    existing_stage = (ticket.before_evidence if stage == "BEFORE" else ticket.after_evidence)
    if digest in {row.get("digest") for row in (existing_stage or [])}:
        db.close()
        raise HTTPException(409, f"this image is already attached as {stage.lower()} evidence")
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[mime]
    stored = config.UPLOADS_DIR / f"{digest}{extension}"
    if not stored.exists():
        stored.write_bytes(raw)
    at = datetime.now(timezone.utc).isoformat()
    evidence = {"digest": digest, "mime": mime, "note": note.strip(),
                "actor": actor.strip(), "at": at,
                "provenance": "STAFF_UPLOADED_PHOTO"}
    if stage == "BEFORE":
        ticket.before_evidence = [*ticket.before_evidence, evidence]
    else:
        ticket.after_evidence = [*ticket.after_evidence, evidence]
        linked_action = _linked_action_for_ticket(db, ticket)
        if linked_action is not None and digest not in {
                event.get("image_sha256") for event in (linked_action.events or [])
                if event.get("event") == "AFTER_EVIDENCE_UPLOADED"}:
            linked_action.events = [*(linked_action.events or []), {
                "at": at, "event": "AFTER_EVIDENCE_UPLOADED",
                "by": actor.strip(), "note": note.strip(),
                "image_sha256": digest,
                "provenance": "STAFF_UPLOADED_PHOTO",
                "ticket_id": ticket.id,
            }]
    ticket.events = [*ticket.events, {"at": at, "event": f"{stage}_EVIDENCE_ADDED",
                                      "by": actor.strip(), "digest": digest}]
    db.commit()
    out = {"ticket_id": ticket.id, "stage": stage, "evidence": evidence,
           "verification_capabilities": _verification_capabilities(
               ticket=ticket, action=_linked_action_for_ticket(db, ticket)),
           }
    db.close()
    return out


@app.post("/api/tickets/{ticket_id}/validate")
def validate_ticket(ticket_id: str, body: TicketValidityBody) -> dict:
    db = SessionLocal()
    ticket = db.get(OperationalTicket, ticket_id)
    if ticket is None:
        db.close()
        raise HTTPException(404, "ticket not found")
    if ticket.status != "PENDING_VALIDATION":
        db.close()
        raise HTTPException(409, "ticket has already been validated")
    if body.verdict == "VALIDATED_ON_SITE" and not ticket.before_evidence:
        db.close()
        raise HTTPException(409, "before photo evidence is required for on-site validation")
    at = datetime.now(timezone.utc).isoformat()
    ticket.validity_status = body.verdict
    ticket.status = "OPEN" if body.verdict == "VALIDATED_ON_SITE" else "DISMISSED"
    ticket.events = [*ticket.events, {"at": at, "event": body.verdict,
                                      "by": body.actor, "note": body.reason}]
    db.commit()
    out = _ticket_dict(ticket)
    db.close()
    return out


@app.post("/api/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: str, body: TicketResolutionBody) -> dict:
    db = SessionLocal()
    ticket = db.get(OperationalTicket, ticket_id)
    if ticket is None:
        db.close()
        raise HTTPException(404, "ticket not found")
    if ticket.status != "OPEN" or ticket.validity_status not in {
            "VALIDATED_ON_SITE", "VALIDATED_BY_FINDING_REVIEW"}:
        db.close()
        raise HTTPException(409, "only a validated open ticket can be resolved")
    if not ticket.after_evidence:
        db.close()
        raise HTTPException(409, "after photo evidence is required before resolution")
    at = datetime.now(timezone.utc).isoformat()
    ticket.status = "RESOLVED_PENDING_VERIFICATION"
    ticket.events = [*ticket.events, {"at": at, "event": "RESOLUTION_SUBMITTED",
                                      "by": body.actor, "note": body.resolution_note}]
    linked_action = _linked_action_for_ticket(db, ticket)
    if linked_action is not None:
        linked_action.status = "COMPLETE_UNVERIFIED"
        linked_action.events = [*linked_action.events, {
            "at": at, "event": "TICKET_RESOLUTION_SUBMITTED",
            "by": body.actor, "ticket_id": ticket.id,
        }]
    db.commit()
    out = _ticket_dict(ticket)
    db.close()
    return out


@app.post("/api/tickets/{ticket_id}/verify")
def verify_ticket(ticket_id: str, body: TicketVerificationBody) -> dict:
    db = SessionLocal()
    ticket = db.get(OperationalTicket, ticket_id)
    if ticket is None:
        db.close()
        raise HTTPException(404, "ticket not found")
    if ticket.status == "CLOSED_VERIFIED":
        out = {**_ticket_dict(ticket), "idempotent": True}
        db.close()
        return out
    if ticket.status != "RESOLVED_PENDING_VERIFICATION":
        db.close()
        raise HTTPException(409, "resolution must be submitted before verification")
    verifier = body.actor.strip().casefold()
    linked_action = _linked_action_for_ticket(db, ticket)
    decision_actors = {
        actor.casefold() for actor in _verification_capabilities(
            ticket=ticket, action=linked_action)["excluded_actors"]
    }
    if verifier in decision_actors:
        db.close()
        raise HTTPException(
            409, "independent verification requires a different actor from validation and resolution")
    now = datetime.now(timezone.utc)
    ticket.status = "CLOSED_VERIFIED"
    ticket.resolved_at = now
    ticket.events = [*ticket.events, {"at": now.isoformat(), "event": "CLOSED_VERIFIED",
                                      "by": body.actor, "note": body.verification_note}]
    if linked_action is not None:
        linked_action.status = "VERIFIED"
        linked_action.events = [*linked_action.events, {
            "at": now.isoformat(), "event": "TICKET_CLOSED_VERIFIED",
            "by": body.actor, "ticket_id": ticket.id,
        }]
    db.commit()
    out = _ticket_dict(ticket)
    db.close()
    return out


@app.post("/api/tickets/{ticket_id}/reply-draft")
def draft_ticket_reply(ticket_id: str) -> dict:
    db = SessionLocal()
    ticket = db.get(OperationalTicket, ticket_id)
    if ticket is None:
        db.close()
        raise HTTPException(404, "ticket not found")
    if ticket.status != "CLOSED_VERIFIED":
        db.close()
        raise HTTPException(409, "owner reply is drafted only after a verified correction")
    comment = (f"Thank you for raising the {ticket.title.lower()} concern. Our team validated "
               "the issue, completed corrective work, and independently verified the result. "
               "We appreciate the feedback and invite you to contact the course directly if "
               "you would like us to follow up further.")
    ticket.external_reply = {
        "status": "DRAFT_AWAITING_BUSINESS_PROFILE_AUTH",
        "comment": comment,
        "private_contact_available": False,
        "note": ("Google exposes an owner reply but not the reviewer's private contact details. "
                 "Publishing requires BroadPeak Business Profile OAuth in production."),
    }
    db.commit()
    out = ticket.external_reply
    db.close()
    return out


def _taxonomy_dict(proposal: TaxonomyProposal) -> dict:
    return {
        "id": proposal.id, "location_id": proposal.location_id,
        "proposed_key": proposal.proposed_key, "label": proposal.label,
        "rationale": proposal.rationale, "example_refs": proposal.example_refs,
        "status": proposal.status, "reviewer": proposal.reviewer,
        "decision_reason": proposal.decision_reason, "events": proposal.events,
        "created_at": proposal.created_at.isoformat(),
        "updated_at": proposal.updated_at.isoformat(),
        "effect": ("Approved for standards-owner design; no standard or model changed automatically."
                   if proposal.status == "APPROVED_FOR_DESIGN" else
                   "No production taxonomy or model change has occurred."),
    }


@app.post("/api/locations/{location_id}/taxonomy/sync")
def sync_taxonomy_proposals(location_id: str) -> dict:
    """Suggest taxonomy gaps from recurring language; never self-modify.

    This is the safe interpretation of continuous learning: the system detects
    a possible new parameter and queues it with examples for a named human to
    approve or reject. Approval still does not create a standard or retrain a
    model; that remains a governed design/deployment step.
    """
    db = SessionLocal()
    loc = db.get(Location, location_id)
    db.close()
    if loc is None:
        raise HTTPException(404, "location not found")
    signal = summarise_themes(location_id, loc.tenant_id)
    captured = signal["sample"].get("captured_at") or "live-sample"
    created: list[str] = []
    existing: list[str] = []
    db = SessionLocal()
    for theme in signal["themes"].get("themes", []):
        gap = _TAXONOMY_GAPS.get(theme.get("theme"))
        if gap is None or int(theme.get("mention_count") or 0) < 2:
            continue
        key, label, rationale = gap
        dedupe_key = hashlib.sha256(f"{location_id}|{captured}|{key}".encode()).hexdigest()
        prior = db.query(TaxonomyProposal).filter_by(dedupe_key=dedupe_key).first()
        if prior:
            existing.append(prior.id)
            continue
        now = datetime.now(timezone.utc).isoformat()
        proposal = TaxonomyProposal(
            id=uid("tax"), tenant_id=loc.tenant_id, location_id=location_id,
            dedupe_key=dedupe_key, proposed_key=key, label=label,
            rationale=rationale, example_refs=theme.get("review_ids") or [],
            events=[{"at": now, "event": "PROPOSED", "by": "SYSTEM",
                     "note": (f"Candidate derived from {theme['mention_count']} recurring review mentions; "
                              "customer context only.")}],
        )
        db.add(proposal)
        created.append(proposal.id)
    db.commit()
    db.close()
    return {"created": created, "existing": existing,
            "guardrail": "No standards or model behaviour changed automatically."}


@app.get("/api/locations/{location_id}/taxonomy")
def list_taxonomy_proposals(location_id: str) -> dict:
    db = SessionLocal()
    if db.get(Location, location_id) is None:
        db.close()
        raise HTTPException(404, "location not found")
    rows = (db.query(TaxonomyProposal).filter_by(location_id=location_id)
              .order_by(TaxonomyProposal.created_at.desc()).all())
    out = {"location_id": location_id, "proposals": [_taxonomy_dict(row) for row in rows],
           "guardrail": "Human approval queues design work; it does not silently retrain or rewrite standards."}
    db.close()
    return out


@app.post("/api/taxonomy/{proposal_id}/decision")
def decide_taxonomy_proposal(proposal_id: str, body: TaxonomyDecisionBody) -> dict:
    db = SessionLocal()
    proposal = db.get(TaxonomyProposal, proposal_id)
    if proposal is None:
        db.close()
        raise HTTPException(404, "taxonomy proposal not found")
    target = "APPROVED_FOR_DESIGN" if body.decision == "APPROVE" else "REJECTED"
    if proposal.status != "PENDING_REVIEW":
        if proposal.status == target:
            out = {**_taxonomy_dict(proposal), "idempotent": True}
            db.close()
            return out
        db.close()
        raise HTTPException(409, "taxonomy proposal has already been decided")
    now = datetime.now(timezone.utc).isoformat()
    proposal.status = target
    proposal.reviewer = body.reviewer
    proposal.decision_reason = body.reason
    proposal.events = [*proposal.events, {"at": now, "event": target,
                                          "by": body.reviewer, "note": body.reason}]
    db.commit()
    out = _taxonomy_dict(proposal)
    db.close()
    return out


@app.get("/api/locations/{location_id}/resolution-analytics")
def resolution_analytics(location_id: str) -> dict:
    db = SessionLocal()
    tickets = db.query(OperationalTicket).filter_by(location_id=location_id).all()
    loc = db.get(Location, location_id)
    if loc is None:
        db.close()
        raise HTTPException(404, "location not found")
    category_counts: dict[str, int] = {}
    durations: list[float] = []
    for ticket in tickets:
        category_counts[ticket.category] = category_counts.get(ticket.category, 0) + 1
        if ticket.resolved_at:
            start = ticket.created_at
            end = ticket.resolved_at
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            durations.append((end - start).total_seconds() / 3600)
    db.close()
    sample = load_review_snapshot(location_id)
    return {
        "location_id": location_id,
        "tickets": {"total": len(tickets),
                    "open": sum(t.status in {"PENDING_VALIDATION", "OPEN",
                                              "RESOLVED_PENDING_VERIFICATION"} for t in tickets),
                    "closed_verified": sum(t.status == "CLOSED_VERIFIED" for t in tickets),
                    "dismissed_not_substantiated": sum(t.status == "DISMISSED" for t in tickets),
                    "mean_time_to_verified_hours": (round(sum(durations) / len(durations), 1)
                                                     if durations else None)},
        "top_issues": [{"category": category, "ticket_count": count}
                       for category, count in sorted(category_counts.items(),
                                                     key=lambda item: (-item[1], item[0]))],
        "rating_impact": {
            "state": "BASELINE_ONLY",
            "baseline": (sample or {}).get("dataset_summary", {}),
            "follow_up_available": False,
            "claim": ("No ROI or rating improvement is claimed until a later comparable "
                      "snapshot exists after verified corrections."),
        },
    }


@app.get("/api/locations/{location_id}/benchmark")
def location_benchmark(location_id: str) -> dict:
    result = competitor_benchmark(location_id)
    if result is None:
        raise HTTPException(404, "no benchmark cohort configured for this location")
    return result


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
    access_events = (db.query(DemoAccessEvent)
                     .order_by(DemoAccessEvent.created_at.desc()).limit(25).all())
    access_total = db.query(DemoAccessEvent).count()
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
        "access_activity": {
            "successful_logins": access_total,
            "webhook_configured": bool(config.LOGIN_NOTIFICATION_WEBHOOK_URL),
            "recent": [{
                "at": event.created_at.isoformat(),
                "username": event.username,
                "visitor_id": event.client_fingerprint,
                "user_agent": event.user_agent,
                "notification_status": event.notification_status,
            } for event in access_events],
        },
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
    if config.APP_ENV == "production":
        # Never expose a database-wide destructive operation on a public URL.
        raise HTTPException(404)
    from .models import Base, engine
    Base.metadata.drop_all(engine)
    init_db()
    seed()
    return {"ok": True}


# ---------------- static frontend ----------------

_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
