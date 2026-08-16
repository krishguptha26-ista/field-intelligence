"""Relational domain model.

A pragmatic POC subset of the full production schema (see docs/adr/ADR-002):
every business table carries tenant_id + timestamps, so the model is
multi-tenant from day one even though the demo shows two fixture tenants.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import config


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="franchise")  # franchise|venue|mobility


class Location(Base, TimestampMixin):
    __tablename__ = "locations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    address: Mapped[str] = mapped_column(String, default="")
    place_id: Mapped[str | None] = mapped_column(String, nullable=True)  # stable Google Place ID
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Zone(Base, TimestampMixin):
    __tablename__ = "zones"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    privacy_level: Mapped[str] = mapped_column(String, default="NORMAL")


class Standard(Base, TimestampMixin):
    """A sourced or representative requirement; ``source_label`` keeps the
    authority class explicit at retrieval and review time."""
    __tablename__ = "standards"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    category: Mapped[str] = mapped_column(String, index=True)   # cleanliness|safety|signage|ops|...
    code: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    severity_default: Mapped[str] = mapped_column(String, default="MEDIUM")
    source_label: Mapped[str] = mapped_column(String, default="REPRESENTATIVE_DEMO_STANDARD")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditSession(Base, TimestampMixin):
    __tablename__ = "audit_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    consultant_name: Mapped[str] = mapped_column(String, default="Field Consultant")
    status: Mapped[str] = mapped_column(String, default="COLLECTING")
    checklist_responses: Mapped[list] = mapped_column(JSON, default=list)


class Observation(Base, TimestampMixin):
    __tablename__ = "observations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    audit_id: Mapped[str] = mapped_column(ForeignKey("audit_sessions.id"), index=True)
    zone_id: Mapped[str | None] = mapped_column(ForeignKey("zones.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String, default="NOTE")  # NOTE|CHECKLIST|PHOTO_DESCRIPTION
    text: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String, default="CONSULTANT_OBSERVATION")
    # For PHOTO_DESCRIPTION: the vision model's raw output, what it declined to
    # assert, and the image digest. Kept beside the text so a reviewer can see
    # that the description came from a model and what it refused to conclude.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class EvidenceItem(Base, TimestampMixin):
    """The evidence envelope (spec §7), flattened for the POC."""
    __tablename__ = "evidence_items"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    source_type: Mapped[str] = mapped_column(String)       # OBSERVATION|GOOGLE_PLACE|PUBLIC_WEB|...
    collection_method: Mapped[str] = mapped_column(String)  # API|UPLOAD|CAMERA|FIXTURE
    provenance: Mapped[str] = mapped_column(String)         # LIVE_API|CACHED_LIVE_DATA|DEMO_FIXTURE|...
    trust_class: Mapped[str] = mapped_column(String, default="CUSTOMER_SIGNAL")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    canonical_url: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(default=now)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ClarificationQuestion(Base, TimestampMixin):
    __tablename__ = "clarification_questions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    audit_id: Mapped[str] = mapped_column(ForeignKey("audit_sessions.id"), index=True)
    observation_id: Mapped[str | None] = mapped_column(ForeignKey("observations.id"), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    why_needed: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="OPEN")  # OPEN|ANSWERED


class Finding(Base, TimestampMixin):
    """Finding contract (spec §7). Human-approved determinations only reach
    APPROVED via reviewer action, never by the model."""
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    audit_id: Mapped[str] = mapped_column(ForeignKey("audit_sessions.id"), index=True)
    observation_id: Mapped[str | None] = mapped_column(ForeignKey("observations.id"), nullable=True, index=True)
    lane: Mapped[str] = mapped_column(String, default="COMPLIANCE_RISK")  # or EXPERIENCE_OPS|GROWTH_OPPORTUNITY
    category: Mapped[str] = mapped_column(String, default="general")
    title: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="READY_FOR_REVIEW")
    standard_id: Mapped[str | None] = mapped_column(ForeignKey("standards.id"), nullable=True)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    contradicting_evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    consultant_statement: Mapped[str] = mapped_column(Text, default="")
    model_interpretation: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String, default="MEDIUM")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    uncertainty_reasons: Mapped[list] = mapped_column(JSON, default=list)
    not_supported: Mapped[list] = mapped_column(JSON, default=list)
    recommended_action: Mapped[dict] = mapped_column(JSON, default=dict)
    review_history: Mapped[list] = mapped_column(JSON, default=list)  # append-only
    # How the agent reached this: the tool calls it made and what came back.
    reasoning_trace: Mapped[list] = mapped_column(JSON, default=list)
    # Deterministic recurrence match against prior verified-closed findings.
    # Computed in Python, not by the model, so the badge can be trusted.
    recurrence: Mapped[dict] = mapped_column(JSON, default=dict)
    # What the adversarial challenge panel argued, and how it voted.
    challenge_record: Mapped[dict] = mapped_column(JSON, default=dict)


class Action(Base, TimestampMixin):
    __tablename__ = "actions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"), index=True, unique=True)
    description: Mapped[str] = mapped_column(Text)
    owner_role: Mapped[str] = mapped_column(String, default="Location Manager")
    due_date: Mapped[str] = mapped_column(String, default="")
    verification_method: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="OPEN")  # OPEN|COMPLETE_UNVERIFIED|VERIFIED
    events: Mapped[list] = mapped_column(JSON, default=list)


class OperationalTicket(Base, TimestampMixin):
    """Closed-loop response to a recurring customer signal.

    A ticket is deliberately not a compliance finding. Public reviews can open
    an operational triage loop, but a person must validate the condition before
    the ticket is treated as substantiated. Before/after evidence and manager
    verification then close the loop.
    """
    __tablename__ = "operational_tickets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    source_kind: Mapped[str] = mapped_column(String, default="CUSTOMER_SIGNAL_THEME")
    source_refs: Mapped[list] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String, default="operations", index=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String, default="MEDIUM")
    assigned_role: Mapped[str] = mapped_column(String, default="Location Manager")
    status: Mapped[str] = mapped_column(String, default="PENDING_VALIDATION", index=True)
    validity_status: Mapped[str] = mapped_column(String, default="UNASSESSED")
    due_date: Mapped[str] = mapped_column(String, default="")
    before_evidence: Mapped[list] = mapped_column(JSON, default=list)
    after_evidence: Mapped[list] = mapped_column(JSON, default=list)
    external_reply: Mapped[dict] = mapped_column(JSON, default=dict)
    events: Mapped[list] = mapped_column(JSON, default=list)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TaxonomyProposal(Base, TimestampMixin):
    """Human-governed suggestion for a new operational parameter.

    Customer language may reveal a gap in the current taxonomy, but it must not
    silently rewrite standards or retrain a production model. The queue makes
    the suggestion, examples and human decision inspectable.
    """
    __tablename__ = "taxonomy_proposals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    proposed_key: Mapped[str] = mapped_column(String, index=True)
    label: Mapped[str] = mapped_column(String)
    rationale: Mapped[str] = mapped_column(Text)
    example_refs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="PENDING_REVIEW", index=True)
    reviewer: Mapped[str] = mapped_column(String, default="")
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    events: Mapped[list] = mapped_column(JSON, default=list)


class ExternalSignal(Base, TimestampMixin):
    """A customer/public signal (review etc.). Context, never proof."""
    __tablename__ = "external_signals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    signal_type: Mapped[str] = mapped_column(String, default="GOOGLE_REVIEW")
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String, default="")
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    provenance: Mapped[str] = mapped_column(String, default="DEMO_FIXTURE")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ModelCall(Base, TimestampMixin):
    """Cost/latency ledger for every LLM invocation (spec §20)."""
    __tablename__ = "model_calls"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    audit_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    purpose: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    schema_retries: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(Base, TimestampMixin):
    """Append-only decision trail."""
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor: Mapped[str] = mapped_column(String)       # user role or SYSTEM/MODEL
    entity_type: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str] = mapped_column(String, index=True)
    event: Mapped[str] = mapped_column(String)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class DemoAccessEvent(Base, TimestampMixin):
    """Privacy-minimised successful access to the shared assessment demo."""
    __tablename__ = "demo_access_events"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    client_fingerprint: Mapped[str] = mapped_column(String, index=True)
    user_agent: Mapped[str] = mapped_column(String, default="")
    notification_status: Mapped[str] = mapped_column(String, default="NOT_CONFIGURED")
    notified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {})


if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _add_missing_columns() -> None:
    """Minimal forward-only migration for the POC's SQLite file.

    `create_all` creates missing tables but never alters existing ones, so an
    already-seeded demo database would silently lack new columns. Alembic is the
    right answer the moment there is a second environment (ADR-002); until then
    this keeps a developer's local database usable without asking anyone to
    delete it.
    """
    from sqlalchemy import inspect, text
    if not config.DATABASE_URL.startswith("sqlite"):
        return
    insp = inspect(engine)
    wanted = {"findings": {"reasoning_trace": "JSON", "recurrence": "JSON",
                           "challenge_record": "JSON"},
              "observations": {"payload": "JSON"}}
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, sqltype in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
