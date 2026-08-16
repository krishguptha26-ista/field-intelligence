"""Principal orchestrator (spec §15): one brain, deterministic tools,
explicit state machine. The model proposes; policy re-checks; humans approve.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from .. import config
from ..budget import ModelBudgetExceeded, audit_budget
from ..field_guide import ZONE_CHECK_CODES, issue_photo_policy
from ..gateway import clear_provider_execution, get_provider, provider_execution
from ..locks import audit_lock
from ..models import (Action, AuditLog, AuditSession, ClarificationQuestion,
                      EvidenceItem, Finding, ModelCall, Observation,
                      OperationalTicket, SessionLocal, Standard, Zone, uid)
from ..regulatory import standard_metadata
from ..schemas import ActionDraft, AnalysisResult, ClarifySpec, FindingDraft
from . import challenge
from . import tools as toolkit

VAGUE_BLOCKLIST = ("a little", "kinda", "kind of", "somewhat", "seemed", "maybe")

MAX_TOOL_STEPS = 6
MAX_TEXT_CLARIFICATIONS = 2
PHOTO_WORKFLOW_PREFIXES = (
    "PHOTO_REQUIRED:",
    "PHOTO_RECOMMENDED:",
    "UNMAPPED_PHOTO_REQUIRED:",
)

_SEVERITY_LADDER = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

_FIELD_ASSIGNEES = {
    "security_presence": "Avery Brooks — Security Supervisor (demo assignee)",
    "cleanliness": "Maya Chen — Facilities Manager (demo assignee)",
    "safety": "Jordan Lee — Safety Manager (demo assignee)",
    "worker_safety": "Jordan Lee — Safety Manager (demo assignee)",
    "course_condition": "Chris Morgan — Director of Golf (demo assignee)",
    "operations": "Taylor Reed — Golf Operations Manager (demo assignee)",
}


def _normalise_question(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower())
            if token not in {"the", "a", "an", "is", "was", "what", "which", "does"}}


def _questions_overlap(left: str, right: str) -> bool:
    a, b = _normalise_question(left), _normalise_question(right)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.72


def _security_reference_ambiguous(value: str) -> bool:
    text = value.lower()
    return (
        bool(re.search(r"\bsecurity\b", text))
        and bool(re.search(r"\bmissing\b|\babsent\b|\bnot (?:there|available|present)\b", text))
        and not bool(re.search(
            r"\bguard\b|\bofficer\b|\bpersonnel\b|\bcamera\b|\bcctv\b|"
            r"\bequipment\b|\balarm\b|\bgate\b|\baccess control\b", text))
    )


def _consultant_answer_excerpt(rows: list[ClarificationQuestion]) -> list[str]:
    """Keep the immutable user record readable when reopening legacy audits.

    New flows are capped at two text turns. Older demo records may contain many
    repetitive answers from the pre-cap loop; retain the first clarifying fact
    and the final settling fact instead of copying internal-looking noise into
    the reviewer-facing consultant statement.
    """
    answers = [str(row.answer).strip() for row in rows if row.answer]
    if len(answers) <= MAX_TEXT_CLARIFICATIONS:
        return answers
    return [answers[0], answers[-1]]


def _photo_ticket_evidence(observation: Observation) -> dict:
    payload = observation.payload or {}
    return {
        "digest": payload.get("image_sha256"),
        "mime": payload.get("mime"),
        "note": observation.text,
        "actor": "Field Consultant",
        "at": observation.created_at.isoformat(),
        "provenance": observation.provenance,
        "observation_id": observation.id,
    }


def _create_field_ticket(db, audit: AuditSession, finding: Finding,
                         observation: Observation,
                         photo_observations: list[Observation]) -> OperationalTicket:
    # The observation id is not a stable incident identity: the same free-form
    # report can later be represented by a reconciled checklist observation.
    # Standard + explicitly linked photos is stable across those representations
    # and the database-unique dedupe key makes concurrent ticket creation fail
    # closed instead of assigning the same work twice.
    photo_ids = sorted({photo.id for photo in photo_observations})
    standard_anchor = finding.standard_id or f"category:{finding.category}"
    dedupe_key = hashlib.sha256(
        f"FIELD_FINDING|{audit.id}|{standard_anchor}|{'|'.join(photo_ids)}".encode()
    ).hexdigest()
    prior = db.query(OperationalTicket).filter_by(dedupe_key=dedupe_key).first()
    if prior is not None:
        prior.source_refs = list(dict.fromkeys([
            *(prior.source_refs or []), finding.id, observation.id, *photo_ids,
        ]))
        known_evidence = {
            (row.get("observation_id"), row.get("digest"))
            for row in (prior.before_evidence or [])
        }
        additions = [
            _photo_ticket_evidence(photo) for photo in photo_observations
            if (photo.id, (photo.payload or {}).get("image_sha256")) not in known_evidence
        ]
        if additions:
            prior.before_evidence = [*(prior.before_evidence or []), *additions]
        return prior
    now = datetime.now(timezone.utc)
    priority = finding.severity if finding.severity in {"HIGH", "CRITICAL"} else "MEDIUM"
    due_days = 2 if priority in {"HIGH", "CRITICAL"} else 5
    ticket = OperationalTicket(
        id=uid("ticket"), tenant_id=audit.tenant_id,
        location_id=audit.location_id, dedupe_key=dedupe_key,
        source_kind="PHOTO_BACKED_FIELD_FINDING",
        source_refs=[finding.id, observation.id, *photo_ids],
        category=finding.category, title=finding.title,
        description=(
            "Photo-backed field observation routed automatically for operator validation. "
            "The photo supports the consultant report; independent review still decides "
            "whether the candidate finding and corrective action are approved."
        ),
        priority=priority,
        assigned_role=_FIELD_ASSIGNEES.get(
            finding.category, "Morgan Patel — Location Manager (demo assignee)"),
        status="PENDING_VALIDATION", validity_status="FIELD_EVIDENCE_ATTACHED",
        due_date=(now + timedelta(days=due_days)).date().isoformat(),
        before_evidence=[_photo_ticket_evidence(photo) for photo in photo_observations],
        events=[{
            "at": now.isoformat(), "event": "AUTO_ROUTED_FROM_FIELD",
            "by": "SYSTEM", "finding_id": finding.id,
            "note": "Photo attached; awaiting operator validation and independent review.",
        }],
    )
    db.add(ticket)
    return ticket


def _existing_incident_finding(db, audit: AuditSession, standard: Standard | None,
                               photo_observations: list[Observation]) -> Finding | None:
    """Return an existing packet for the same standard and explicit photo set.

    Findings remain anchored to the immutable consultant observation.  That is
    correct for provenance but it means observation id alone cannot deduplicate
    a later checklist representation of the same incident.  Canonical photo
    evidence plus the controlled standard is the narrowest safe equivalence:
    separate photos remain separate incidents, while a resubmitted checklist
    cannot manufacture a second packet from the exact same evidence.
    """
    if standard is None or not photo_observations:
        return None
    photo_observation_ids = {photo.id for photo in photo_observations}
    photo_evidence_ids = {
        evidence.id for evidence in db.query(EvidenceItem).filter_by(
            tenant_id=audit.tenant_id, location_id=audit.location_id,
        ).all()
        if (evidence.payload or {}).get("observation_id") in photo_observation_ids
        and evidence.source_type in {"PHOTO", "VIDEO"}
    }
    if not photo_evidence_ids:
        return None
    candidates = db.query(Finding).filter_by(
        audit_id=audit.id, standard_id=standard.id,
    ).order_by(Finding.created_at.asc()).all()
    return next((finding for finding in candidates
                 if photo_evidence_ids.intersection(finding.evidence_ids or [])), None)


def _link_checklist_row_to_finding(audit: AuditSession, observation: Observation,
                                   standard: Standard, finding: Finding,
                                   photo_observations: list[Observation]) -> None:
    """Make a suppressed duplicate visible in the checklist JSON record."""
    photo_ids = {photo.id for photo in photo_observations}
    rows = list(audit.checklist_responses or [])
    changed = False
    for index, row in enumerate(rows):
        if (row.get("zone_id") != observation.zone_id
                or row.get("standard_code") != standard.code):
            continue
        row_evidence = set(row.get("evidence_observation_ids") or [])
        if photo_ids and not photo_ids.intersection(row_evidence):
            continue
        rows[index] = {
            **row,
            "finding_id": finding.id,
            "canonical_incident_deduplicated": True,
            "review_required": True,
        }
        changed = True
    if changed:
        audit.checklist_responses = rows


def _reconcile_checklist_from_finding(db, audit: AuditSession, finding: Finding,
                                      standard: Standard | None,
                                      observation: Observation,
                                      photo_observations: list[Observation]) -> bool:
    # A typed checklist failure is already the consultant's explicit answer.
    # Reconciliation is only for free-form field reports, otherwise we would
    # relabel a human-entered response as an automatic system assertion.
    if observation.kind == "CHECKLIST" or standard is None or not observation.zone_id:
        return False
    zone = db.get(Zone, observation.zone_id)
    if zone is None or standard.code not in ZONE_CHECK_CODES.get(zone.name, []):
        return False
    key = (zone.id, standard.code)
    rows = list(audit.checklist_responses or [])
    prior = next((row for row in rows
                  if (row.get("zone_id"), row.get("standard_code")) == key), None)
    if prior is not None:
        # Never let a model-derived field match silently rewrite an explicit
        # consultant response. Surface the contradiction and require the
        # consultant to choose which record is correct.
        conflicted = {
            **prior,
            "reconciliation_conflict": {
                "status": "PENDING_CONSULTANT_CONFIRMATION",
                "finding_id": finding.id,
                "observation_id": observation.id,
                "reported_detail": finding.consultant_statement,
                "evidence_observation_ids": [photo.id for photo in photo_observations],
                "suggested_response": "FAIL",
            },
            "review_required": True,
        }
        rows = [row for row in rows
                if (row.get("zone_id"), row.get("standard_code")) != key]
        rows.append(conflicted)
        audit.checklist_responses = rows
        db.add(AuditLog(
            id=uid("log"), tenant_id=audit.tenant_id, actor="SYSTEM",
            entity_type="audit", entity_id=audit.id,
            event="CHECKLIST_CONFLICT_REQUIRES_CONFIRMATION",
            detail={"finding_id": finding.id, "observation_id": observation.id,
                    "zone_id": zone.id, "standard_code": standard.code,
                    "preserved_response": prior.get("response")},
        ))
        return False
    reconciled = {
        "item": standard.text,
        "standard_code": standard.code,
        "response": "FAIL",
        "detail": finding.consultant_statement,
        "zone_id": zone.id,
        "evidence_observation_ids": [photo.id for photo in photo_observations],
        "photo_decision": (
            "ATTACHED" if photo_observations else "CONTINUE_WITHOUT_PHOTO"),
        "photo_policy": issue_photo_policy(
            standard.code,
            category=standard.category,
            severity=finding.severity,
        ),
        "source_label": standard.source_label,
        "standard_metadata": standard_metadata(standard.code),
        "verification_state": (
            "PHOTO_ATTACHED_PENDING_REVIEW" if photo_observations else
            "CONSULTANT_REPORTED_PHOTO_RECOMMENDED"),
        "auto_reconciled": True,
        "review_required": True,
        "finding_id": finding.id,
    }
    rows.append(reconciled)
    audit.checklist_responses = rows
    db.add(AuditLog(
        id=uid("log"), tenant_id=audit.tenant_id, actor="SYSTEM",
        entity_type="audit", entity_id=audit.id,
        event="CHECKLIST_RECONCILED_FROM_FIELD_FINDING",
        detail={"finding_id": finding.id, "observation_id": observation.id,
                "zone_id": zone.id, "standard_code": standard.code,
                "prior_response": None,
                "review_required": True},
    ))
    return True


def _scope_representative_standard(finding: FindingDraft, standard: Standard | None) -> list[str]:
    """Prevent demo guidance from being narrated as an official violation.

    Prompt wording is not an enforcement boundary. BroadPeak supplied no
    authoritative standards, so the stored interpretation is deterministically
    scoped even when a model uses stronger compliance language.
    """
    if standard is None:
        return []
    source = (standard.source_label or "").upper()
    if "REPRESENTATIVE" not in source:
        if any(token in source for token in
               ("FEDERAL_REQUIREMENT", "GEORGIA_REQUIREMENT", "COUNTY/STATE", "GEORGIA CODE")):
            finding.model_interpretation = re.sub(
                r"\b(?:violates?|breaches?|is non[- ]compliant with)\s+(?:the\s+)?(?:standard|requirement)\b",
                "may be inconsistent with the cited external requirement",
                finding.model_interpretation,
                flags=re.IGNORECASE,
            )
            boundary = (
                "External source retrieved; exact legal applicability and any compliance "
                "determination require qualified human review."
            )
        else:
            finding.model_interpretation = re.sub(
                r"\b(?:violates?|breaches?|is non[- ]compliant with)\s+(?:the\s+)?(?:standard|requirement)\b",
                "is inconsistent with the cited guidance or published policy",
                finding.model_interpretation,
                flags=re.IGNORECASE,
            )
            boundary = (
                "This cited source is guidance or published policy, not an independent "
                "legal determination."
            )
        if boundary.lower() not in finding.model_interpretation.lower():
            finding.model_interpretation = f"{finding.model_interpretation.rstrip()} {boundary}"
        return [boundary]
    finding.model_interpretation = re.sub(
        r"\b(?:violates?|breaches?|is non[- ]compliant with)\s+(?:the\s+)?standard\b",
        "is inconsistent with this representative guide",
        finding.model_interpretation,
        flags=re.IGNORECASE,
    )
    boundary = (
        "Representative demo guidance only; BroadPeak did not supply an "
        "authoritative standard or confirm applicability."
    )
    if boundary.lower() not in finding.model_interpretation.lower():
        finding.model_interpretation = f"{finding.model_interpretation.rstrip()} {boundary}"
    return [boundary]


def _policy_check(decision, observation_text: str) -> tuple[bool, str]:
    """Deterministic re-check of the model's decision (belt and braces).

    Returns (allowed, reason). A CANDIDATE_FINDING is demoted to CLARIFY when
    the underlying text is vague with no specific observable condition —
    even if the model was confident. The unsupported-finding rate is the
    metric that matters (spec §22.4)."""
    if decision.decision != "CANDIDATE_FINDING":
        return True, ""
    text = observation_text.lower()
    has_vague = any(v in text for v in VAGUE_BLOCKLIST)
    f = decision.finding
    if f is None:
        return False, "CANDIDATE_FINDING without finding payload"
    if has_vague and f.confidence < 0.85 and not f.uncertainty_reasons:
        return False, "vague wording without stated uncertainty"
    if not f.standard_code:
        return False, "no standard cited"
    return True, ""


def _grounding_check(decision, ctx: toolkit.ToolContext,
                     valid_codes: set[str]) -> tuple[bool, str]:
    """A finding may only cite a standard the agent actually retrieved.

    This is the check that separates a cited standard from a remembered one.
    `CLN-01` is a plausible-looking code; a model that has seen a few brand
    manuals can produce one that does not exist in this tenant, attached to
    confident prose, and nothing downstream would notice. Two gates:

      * the code must exist and be active for this tenant, and
      * it must have come back from a `search_standards` call in THIS run.

    Failing either demotes the finding to a clarifying question. The model is
    free to be wrong; it is not free to be unverifiable.
    """
    if decision.decision != "CANDIDATE_FINDING" or decision.finding is None:
        return True, ""
    code = decision.finding.standard_code
    if code not in valid_codes:
        return False, f"cited standard '{code}' does not exist for this tenant"
    if code not in ctx.retrieved_standard_codes:
        return False, (f"cited standard '{code}' was never returned by a "
                       f"search_standards call in this run (ungrounded citation)")
    return True, ""


_SIGNAL_MENTION = re.compile(
    r"\b(customer|public|guest|online)\s+(feedback|review|reviews|sentiment|comments?)\b"
    r"|\breview(s)?\s+(mention|indicate|suggest|corroborat)", re.I)
_NON_CAUSAL = re.compile(r"does not prove|not proof|cannot prove|does not establish", re.I)

_SIGNAL_QUALIFIER = (" [Policy note: the public customer signals referred to above are "
                     "context only — consistent with, but not proof of, this finding. "
                     "The finding rests on the field observation and the cited standard "
                     "alone.]")


def _signal_language_check(f) -> bool:
    """Enforce the non-causal hedge whenever an interpretation leans on reviews.

    Observed in testing: given review context, the model writes a clean,
    evidence-based interpretation and then closes with a bare "customer feedback
    also indicates similar concerns." Nothing about that is factually wrong, and
    the review is correctly absent from the evidence list — but a franchisee
    reading it sees public sentiment cited against them without qualification,
    which is exactly the line this product is not allowed to cross.

    A prompt instruction is not sufficient here. It competes with a long
    transcript and loses often enough to matter, and this is the one rule where
    a soft failure damages the relationship the tool exists to protect. So the
    hedge is appended deterministically and labelled as a policy note rather
    than passed off as the model's own words. Returns True when it fired.
    """
    text = f.model_interpretation or ""
    if not _SIGNAL_MENTION.search(text) or _NON_CAUSAL.search(text):
        return False
    f.model_interpretation = text.rstrip() + _SIGNAL_QUALIFIER
    if not any("context only" in s.lower() for s in f.not_supported):
        f.not_supported.append(
            "That public reviews describe the same condition — customer signals are "
            "context and cannot evidence this finding.")
    return True


def _detect_recurrence(db, audit, observation, category: str) -> dict:
    """Has this location been found for this before, and was it signed off?

    Computed in Python rather than trusted from the model, because the badge it
    drives changes how a franchisee is treated. A recurrence after a verified
    close is a process failure, and the reviewer should see that claim only
    when the records actually support it.
    """
    prior_audits = [a.id for a in db.query(AuditSession)
                    .filter_by(location_id=audit.location_id).all() if a.id != audit.id]
    if not prior_audits:
        return {}
    prior = [f for f in db.query(Finding).filter(Finding.audit_id.in_(prior_audits),
                                                 Finding.category == category).all()
             if f.status == "APPROVED"]
    if not prior:
        return {}
    acts = {a.finding_id: a for a in db.query(Action)
            .filter(Action.finding_id.in_([f.id for f in prior])).all()}
    now = datetime.now(timezone.utc)
    closed = [(f, acts[f.id]) for f in prior
              if f.id in acts and acts[f.id].status == "VERIFIED"]
    if not closed:
        return {"prior_count": len(prior), "closed_and_verified": False,
                "summary": f"{len(prior)} prior finding(s) in {category} at this location, "
                           f"none verified closed."}
    f, a = max(closed, key=lambda p: p[0].created_at)
    created = f.created_at.replace(tzinfo=timezone.utc) if f.created_at.tzinfo is None else f.created_at
    return {"prior_count": len(prior), "closed_and_verified": True,
            "prior_finding_id": f.id, "prior_title": f.title,
            "days_since_prior": (now - created).days,
            "corrective_action": a.description,
            "summary": (f"Recurrence: a {category} finding at this location was verified "
                        f"closed {(now - created).days} days ago and the condition has "
                        f"returned. The corrective action did not hold.")}


def _escalate(severity: str) -> str:
    i = _SEVERITY_LADDER.index(severity) if severity in _SEVERITY_LADDER else 2
    return _SEVERITY_LADDER[min(i + 1, len(_SEVERITY_LADDER) - 1)]


def _analysis_state_digest(db, audit: AuditSession) -> str:
    """Fingerprint only state that can change the analysis outcome."""
    observations = db.query(Observation).filter_by(audit_id=audit.id).order_by(
        Observation.id).all()
    questions = db.query(ClarificationQuestion).filter_by(audit_id=audit.id).order_by(
        ClarificationQuestion.id).all()
    findings = db.query(Finding).filter_by(audit_id=audit.id).order_by(Finding.id).all()
    state = {
        "checklist": audit.checklist_responses or {},
        "observations": [
            [row.id, row.kind, row.zone_id, row.text, row.payload or {}]
            for row in observations
        ],
        "questions": [
            [row.id, row.observation_id, row.question, row.answer, row.status]
            for row in questions
        ],
        "findings": [[row.id, row.status] for row in findings],
    }
    encoded = json.dumps(state, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def analyze_audit(audit_id: str) -> dict:
    with audit_lock(audit_id):
        return _analyze_audit_unlocked(audit_id)


def _analyze_audit_unlocked(audit_id: str) -> dict:
    """Run the full analysis pass over an audit's observations."""
    clear_provider_execution(audit_id)
    db = SessionLocal()
    audit = db.get(AuditSession, audit_id)
    if audit is None:
        db.close()
        raise ValueError("audit not found")

    current_digest = _analysis_state_digest(db, audit)
    previous = (db.query(AuditLog).filter_by(
        entity_type="audit", entity_id=audit_id,
        event="ANALYSIS_STATE_COMPLETED").order_by(AuditLog.created_at.desc()).first())
    if previous is not None and (previous.detail or {}).get("state_digest") == current_digest:
        status = audit.status
        db.close()
        return {
            "summary": "No evidence or clarification changed since the last completed analysis.",
            "findings": [], "clarifications": [], "no_issue": [], "demoted": [],
            "tool_calls": 0, "investigation_stopped": "IDEMPOTENT_NO_CHANGE",
            "idempotent": True, "audit_status": status,
        }

    # Budget actual model invocations, not analysis-button presses. A single
    # analysis can invoke investigation, decision, and three challenge lenses.
    budget = audit_budget(db, audit_id)
    if budget["remaining_calls"] <= 0:
        db.close()
        raise ModelBudgetExceeded(
            "per-audit analysis budget exceeded — acknowledge in UI to continue"
        )

    observations = db.query(Observation).filter_by(audit_id=audit_id).all()
    checklist_linked_media_ids = {
        evidence_id
        for observation in observations if observation.kind == "CHECKLIST"
        for evidence_id in ((observation.payload or {}).get(
            "evidence_observation_ids") or [])
    }
    # Model-generated voice transcripts remain drafts until the consultant has
    # reviewed/edited them. Unconfirmed speech can never become a finding.
    # A standalone photo becomes corroboration, not a competing model-authored
    # incident, once the consultant explicitly links it to a checklist answer.
    observations = [o for o in observations
                    if not (o.payload or {}).get("awaiting_confirmation")
                    and not (o.payload or {}).get("support_only")
                    and not (o.kind in {"PHOTO_DESCRIPTION", "VIDEO_DESCRIPTION"}
                             and o.id in checklist_linked_media_ids)]
    standards = db.query(Standard).filter_by(tenant_id=audit.tenant_id, active=True).all()
    questions = (db.query(ClarificationQuestion)
                   .filter_by(audit_id=audit_id)
                   .order_by(ClarificationQuestion.created_at.asc()).all())
    question_history: dict[str, list[ClarificationQuestion]] = {}
    for question in questions:
        if question.observation_id:
            question_history.setdefault(question.observation_id, []).append(question)
    latest_qs = {observation_id: rows[-1]
                 for observation_id, rows in question_history.items()}
    open_qs = {observation_id: next(
        (row for row in reversed(rows) if row.status == "OPEN"), None)
        for observation_id, rows in question_history.items()}
    open_qs = {observation_id: row for observation_id, row in open_qs.items()
               if row is not None}

    # Idempotency: an observation that already produced a finding is settled.
    # Re-analysis must never duplicate it (and we save the tokens too).
    settled = {f.observation_id for f in db.query(Finding).filter_by(audit_id=audit_id).all()
               if f.observation_id}
    for log in db.query(AuditLog).filter(
            AuditLog.entity_type == "audit",
            AuditLog.entity_id == audit_id,
            AuditLog.event.in_({
                "FIELD_CONCERN_ESCALATED",
                "FINDING_CANONICAL_INCIDENT_DEDUPED",
            })).all():
        observation_id = (log.detail or {}).get("observation_id")
        if observation_id:
            settled.add(observation_id)
    observations = [o for o in observations if o.id not in settled]
    if not observations:
        db.close()
        return {"summary": "All observations already analysed; nothing new to process.",
                "findings": [], "clarifications": [], "no_issue": [], "demoted": [],
                "audit_status": audit.status}

    # The standards corpus is deliberately NOT dumped into the prompt any more.
    # The agent has to go and look it up, which is what makes the citation
    # grounding check below meaningful.
    payload = {
        "location_id": audit.location_id,
        "checklist_responses": audit.checklist_responses,
        "observations": [
            {"id": o.id, "kind": o.kind, "zone_id": o.zone_id, "text": o.text,
             "clarification_answer": "\n".join(
                 row.answer for row in question_history.get(o.id, [])
                 if row.answer and not row.why_needed.startswith(
                     PHOTO_WORKFLOW_PREFIXES)
             ) or None,
             "clarification_history": [
                 {"question": row.question, "answer": row.answer}
                 for row in question_history.get(o.id, [])
                 if row.answer and not row.why_needed.startswith(
                     PHOTO_WORKFLOW_PREFIXES)
             ]}
            for o in observations],
        "standard_categories": sorted({s.category for s in standards}),
    }

    # ---- phase 1: investigate (read-only tools, bounded) ----
    ctx = toolkit.ToolContext(tenant_id=audit.tenant_id, location_id=audit.location_id,
                              audit_id=audit_id)
    investigate_doc = (config.PROMPTS_DIR / "investigation.md").read_text()
    provider = get_provider()
    inv = provider.investigate(
        purpose="audit_analysis",
        prompt=f"{investigate_doc}\n\nINPUT_JSON:{json.dumps(payload)}",
        tool_declarations=toolkit.declarations(),
        execute=lambda name, args: toolkit.execute(name, args, ctx),
        tenant_id=audit.tenant_id, audit_id=audit_id, max_steps=MAX_TOOL_STEPS)

    # If the agent proposed no retrieval at all, every citation would fail
    # grounding and the whole audit would collapse into clarifications. Retrieve
    # deterministically instead, and label it in the trace so nobody reads the
    # fallback as the agent's own initiative.
    if not ctx.retrieved_standard_codes:
        for o in observations:
            args = {"query": o.text}
            inv["trace"].append({"step": 0, "tool": "search_standards", "args": args,
                                 "result": toolkit.execute("search_standards", args, ctx),
                                 "actor": "SYSTEM_FALLBACK",
                                 "note": "Agent requested no retrieval; system retrieved "
                                         "candidates so citations remain checkable."})

    # ---- phase 2: decide (schema-locked, no tools) ----
    prompt_doc = (config.PROMPTS_DIR / "audit_analysis.md").read_text()
    prompt = (f"{prompt_doc}\n\nINPUT_JSON:{json.dumps(payload)}"
              f"\n\nINVESTIGATION_RESULTS:{json.dumps(inv['trace'])[:24000]}")

    result: AnalysisResult = provider.generate(
        purpose="audit_analysis", prompt=prompt, schema=AnalysisResult,
        tenant_id=audit.tenant_id, audit_id=audit_id)
    execution = provider_execution(audit_id)

    std_by_code = {s.code: s for s in standards}
    valid_codes = set(std_by_code)
    created: dict = {"findings": [], "clarifications": [], "no_issue": [], "demoted": [],
                     "tool_calls": len(inv["trace"]), "investigation_stopped": inv["stopped"]}

    eligible = {o.id: o for o in observations}
    processed: set[str] = set()
    for d in result.decisions:
        ob = eligible.get(d.observation_id)
        # Ignore cross-audit and duplicate decisions. A model cannot attach a
        # decision to evidence outside the current audit or create two findings
        # from one observation in the same run.
        if ob is None or ob.id in processed:
            continue
        processed.add(ob.id)
        # The evidence for an observation is what the consultant wrote PLUS any
        # clarification they have since answered. Every downstream check has to
        # see the whole of it: judging the merged evidence against the original
        # vague wording alone is how a question that was properly answered gets
        # thrown away for being vague.
        answered_rows = [row for row in question_history.get(ob.id, [])
                         if row.answer and not row.why_needed.startswith(
                             PHOTO_WORKFLOW_PREFIXES)]
        answered = "\n".join(row.answer for row in answered_rows)
        evidence_text = f"{ob.text}\n{answered}".strip() if answered else ob.text
        if _security_reference_ambiguous(evidence_text):
            # "Security is missing" can mean a person, camera, alarm, gate or
            # process. Never let a model silently choose the guard standard.
            d.decision = "CLARIFY"
            d.finding = None
            d.clarify = ClarifySpec(
                question=("What exactly was missing at the entrance: the scheduled "
                          "guard/officer, security equipment, or both?"),
                why_needed=("The original wording does not identify whether this is a "
                            "staffing issue or an equipment issue."),
                options=[
                    "The scheduled guard or officer was absent",
                    "Security equipment was missing or not working",
                    "Both personnel and equipment were affected",
                ],
            )
        ok, reason = _policy_check(d, evidence_text)
        if ok:
            ok, reason = _grounding_check(d, ctx, valid_codes)
        if not ok and d.decision == "CANDIDATE_FINDING":
            created["demoted"].append({"observation_id": ob.id, "reason": reason})
            d.decision = "CLARIFY"
            if d.clarify is None:  # demotion must still leave the reviewer something to act on
                d.clarify = ClarifySpec(
                    question=("The evidence as written could not be tied to a verified "
                              "standard for this location. What exactly was observed, "
                              "and in which zone?"),
                    why_needed=f"Automatic policy demotion: {reason}")

        if d.decision == "CLARIFY":
            existing_open = open_qs.get(ob.id)
            proposed = (d.clarify.question if d.clarify else
                        "What additional observable detail would settle this assessment?")
            prior_rows = question_history.get(ob.id, [])
            prior_text_rows = [row for row in prior_rows
                               if not row.why_needed.startswith(
                                   PHOTO_WORKFLOW_PREFIXES)]
            repeated = any(_questions_overlap(proposed, row.question) for row in prior_text_rows)
            if existing_open:
                pass  # never create more than one open question for an observation
            elif repeated or len([row for row in prior_text_rows if row.answer]) >= MAX_TEXT_CLARIFICATIONS:
                q = ClarificationQuestion(
                    id=uid("q"), tenant_id=audit.tenant_id, audit_id=audit_id,
                    observation_id=ob.id,
                    question=("Add one clear photo of the reported condition so it can be "
                              "routed as a field concern without another repetitive question."),
                    why_needed=("UNMAPPED_PHOTO_REQUIRED: Two clarification turns did not "
                                "produce a grounded standard match. The report is preserved "
                                "and will route to operations for validation, not be labelled "
                                "a compliance finding."),
                    options=[])
                db.add(q)
                open_qs[ob.id] = q
                created["clarifications"].append(q.id)
                created.setdefault("bounded_clarifications", []).append(ob.id)
            elif latest_qs.get(ob.id) is None or latest_qs[ob.id].answer:
                q = ClarificationQuestion(
                    id=uid("q"), tenant_id=audit.tenant_id, audit_id=audit_id,
                    observation_id=ob.id,
                    question=proposed,
                    why_needed=(d.clarify.why_needed if d.clarify else ""),
                    options=(d.clarify.options if d.clarify else []))
                db.add(q)
                open_qs[ob.id] = q
                question_history.setdefault(ob.id, []).append(q)
                created["clarifications"].append(q.id)
        elif d.decision == "CANDIDATE_FINDING" and d.finding:
            f = d.finding
            std = std_by_code.get(f.standard_code)
            photo_policy = issue_photo_policy(
                f.standard_code,
                category=(std.category if std else f.category),
                severity=f.severity,
            )
            explicitly_linked_photo_ids = set(
                (ob.payload or {}).get("evidence_observation_ids") or [])
            supporting_photos = ([ob] if ob.kind == "PHOTO_DESCRIPTION" else []) + [
                candidate for candidate in db.query(Observation).filter_by(
                    audit_id=audit_id, kind="PHOTO_DESCRIPTION").all()
                if ((candidate.payload or {}).get("supports_observation_id") == ob.id
                    or candidate.id in explicitly_linked_photo_ids)
            ]
            supporting_photos = list({photo.id: photo for photo in supporting_photos}.values())
            if not supporting_photos:
                prior_photo_choice = any(
                    row.answer and row.why_needed.startswith("PHOTO_RECOMMENDED:")
                    for row in question_history.get(ob.id, [])
                ) or (ob.payload or {}).get("photo_decision") == "CONTINUE_WITHOUT_PHOTO"
                if photo_policy["level"] == "REQUIRED":
                    pending_photo = next((row for row in question_history.get(ob.id, [])
                                          if row.status == "OPEN" and
                                          row.why_needed.startswith("PHOTO_REQUIRED:")), None)
                    if pending_photo is None:
                        q = ClarificationQuestion(
                            id=uid("q"), tenant_id=audit.tenant_id, audit_id=audit_id,
                            observation_id=ob.id,
                            question="Take one clear photo that shows this reported issue.",
                            why_needed=("PHOTO_REQUIRED: " + photo_policy["reason"] + " The "
                                        "photo supports the report; it does not itself prove "
                                        "a violation."),
                            options=[],
                        )
                        db.add(q)
                        question_history.setdefault(ob.id, []).append(q)
                        open_qs[ob.id] = q
                        created["clarifications"].append(q.id)
                        created.setdefault("evidence_requests", []).append(ob.id)
                    continue
                if not prior_photo_choice:
                    pending_recommendation = next((
                        row for row in question_history.get(ob.id, [])
                        if row.status == "OPEN" and
                        row.why_needed.startswith("PHOTO_RECOMMENDED:")
                    ), None)
                    if pending_recommendation is None:
                        q = ClarificationQuestion(
                            id=uid("q"), tenant_id=audit.tenant_id, audit_id=audit_id,
                            observation_id=ob.id,
                            question=("AI recommends one supporting photo for this report. "
                                      "Would you like to attach it or continue with your "
                                      "detailed text at lower confidence?"),
                            why_needed=("PHOTO_RECOMMENDED: " + photo_policy["reason"]),
                            options=[],
                        )
                        db.add(q)
                        question_history.setdefault(ob.id, []).append(q)
                        open_qs[ob.id] = q
                        created["clarifications"].append(q.id)
                        created.setdefault("evidence_recommendations", []).append(ob.id)
                    continue

            existing_incident = _existing_incident_finding(
                db, audit, std, supporting_photos)
            if existing_incident is not None:
                if std is not None and ob.kind == "CHECKLIST":
                    _link_checklist_row_to_finding(
                        audit, ob, std, existing_incident, supporting_photos)
                db.add(AuditLog(
                    id=uid("log"), tenant_id=audit.tenant_id, actor="SYSTEM",
                    entity_type="audit", entity_id=audit.id,
                    event="FINDING_CANONICAL_INCIDENT_DEDUPED",
                    detail={
                        "observation_id": ob.id,
                        "canonical_finding_id": existing_incident.id,
                        "standard_code": std.code if std else None,
                        "photo_observation_ids": sorted(
                            photo.id for photo in supporting_photos),
                    },
                ))
                created.setdefault("deduplicated_findings", []).append({
                    "observation_id": ob.id,
                    "finding_id": existing_incident.id,
                    "standard_code": std.code if std else None,
                })
                continue

            # Run the challenge before adding/flushing finding evidence below.
            # Model-call ledger writes use a separate session; once this SQLite
            # transaction has autoflushed an EvidenceItem, those writes would
            # contend with our own lock and all three challengers would abstain.
            panel = challenge.run_panel(
                f, observation_text=evidence_text,
                standard=({"code": std.code, "text": std.text, "category": std.category}
                          if std else None),
                tenant_id=audit.tenant_id, audit_id=audit_id)
            if panel.get("outcome") in {"OVERTURNED", "INCONCLUSIVE"}:
                # The panel killed it. The reviewer gets the question the
                # challengers said would settle it, not a finding they would
                # have had to reject themselves.
                created["demoted"].append(
                    {"observation_id": ob.id,
                     "reason": ("challenge panel unavailable/inconclusive" if
                                panel.get("outcome") == "INCONCLUSIVE" else
                                "overturned by challenge panel"),
                     "votes": panel["votes"],
                     "arguments": [c["argument"] for c in panel["challenges"]
                                   if c["verdict"] == "OVERTURN"]})
                settle = [s for s in panel.get("settling_evidence", []) if s]
                pending = open_qs.get(ob.id)
                if pending is not None and not pending.answer:
                    pass  # an unanswered question is already in front of the consultant
                else:
                    q = ClarificationQuestion(
                        id=uid("q"), tenant_id=audit.tenant_id, audit_id=audit_id,
                        observation_id=ob.id,
                        question=(settle[0] if settle else
                                  "What additional detail would confirm this condition?"),
                        why_needed=("An internal challenge panel found this observation did not "
                                    "yet support a finding. Rather than put a contestable finding "
                                    "in front of a reviewer, the system is asking first."),
                        options=[])
                    db.add(q)
                    created["clarifications"].append(q.id)
                created.setdefault("challenge_outcomes", []).append(
                    {"observation_id": ob.id, "outcome": panel.get("outcome"),
                     **panel["votes"]})
                continue

            challenge_notes = challenge.apply_outcome(f, panel)
            authority_notes = _scope_representative_standard(f, std)
            created.setdefault("challenge_outcomes", []).append(
                {"observation_id": ob.id, "outcome": panel.get("outcome"),
                 **panel.get("votes", {})})

            # Reuse the canonical photo evidence envelope when one exists so
            # its digest and provenance survive into the finding. Direct notes
            # and checklist entries receive capture-accurate provenance.
            ev = next((e for e in db.query(EvidenceItem)
                       .filter_by(location_id=audit.location_id).all()
                       if (e.payload or {}).get("observation_id") == ob.id), None)
            if ev is None:
                source_type = "CHECKLIST" if ob.kind == "CHECKLIST" else "CONSULTANT_NOTE"
                ev = EvidenceItem(
                    id=uid("ev"), tenant_id=audit.tenant_id,
                    location_id=audit.location_id,
                    source_type=source_type, collection_method="DIRECT_ENTRY",
                    provenance="CONSULTANT_OBSERVATION", trust_class="CONSULTANT_ATTESTATION",
                    excerpt=evidence_text, observed_at=ob.created_at,
                    payload={"observation_id": ob.id, "kind": ob.kind,
                             "clarification_included": bool(answered),
                             "verification_state": (ob.payload or {}).get(
                             "verification_state", "CONSULTANT_REPORTED")})
                db.add(ev)

            # A failed checklist can be corroborated by photo/video observations.
            # Preserve those canonical evidence envelopes on the finding itself;
            # retaining only their observation ids in checklist payload would make
            # the UI's MEDIA_CORROBORATED label stronger than the reviewable record.
            linked_observation_ids = list(dict.fromkeys(
                [*((ob.payload or {}).get("evidence_observation_ids") or []),
                 *[photo.id for photo in supporting_photos]]
            ))
            linked_evidence: list[EvidenceItem] = []
            if linked_observation_ids:
                linked_evidence = [
                    candidate
                    for candidate in db.query(EvidenceItem).filter_by(
                        tenant_id=audit.tenant_id,
                        location_id=audit.location_id,
                    ).all()
                    if (candidate.payload or {}).get("observation_id")
                    in linked_observation_ids
                    and candidate.source_type in {"PHOTO", "VIDEO"}
                ]
            finding_evidence_ids = [ev.id, *[item.id for item in linked_evidence]]

            category = std.category if std else f.category
            if _signal_language_check(f):
                created.setdefault("policy_annotations", []).append(
                    {"observation_id": ob.id, "rule": "customer_signal_non_causal_language"})
            recurrence = _detect_recurrence(db, audit, ob, category)
            severity = f.severity
            confidence = f.confidence
            uncertainty = list(f.uncertainty_reasons) + challenge_notes + authority_notes
            if execution["degraded"]:
                uncertainty.append(
                    "Gemini was unavailable during this analysis; a labelled deterministic "
                    "fixture fallback prepared the candidate. Human review must treat the AI "
                    "interpretation as degraded."
                )
            if not supporting_photos:
                uncertainty.append(
                    "No supporting photo was attached. The consultant chose to continue "
                    "with detailed text; human review must account for the lower evidence confidence."
                )
                confidence = min(confidence, 0.70)
            if recurrence.get("closed_and_verified"):
                severity = _escalate(severity)
                uncertainty.append(
                    f"Severity raised one level from {f.severity}: this is a repeat of a "
                    f"finding verified closed {recurrence['days_since_prior']} days ago.")

            due = (datetime.now(timezone.utc) + timedelta(days=f.recommended_action.due_in_days)).date().isoformat()
            # Only the tool calls that touched this observation's subject matter,
            # so the reviewer sees a readable trace instead of the whole run.
            relevant_trace = [t for t in inv["trace"]
                              if ob.text[:40].lower() in json.dumps(t.get("args", {})).lower()
                              or t.get("tool") != "search_standards"]
            standard_snapshot = ({
                "step": 0,
                "tool": "standard_snapshot",
                "actor": "POLICY",
                "result": {
                    "code": std.code,
                    "text": std.text,
                    "category": std.category,
                    "source_label": std.source_label,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    **standard_metadata(std.code),
                },
            } if std else None)
            consultant_answers = _consultant_answer_excerpt(answered_rows)
            finding = Finding(
                id=uid("finding"), tenant_id=audit.tenant_id, audit_id=audit_id,
                observation_id=ob.id,
                lane=f.lane, category=category, title=f.title,
                status="READY_FOR_REVIEW",
                standard_id=(std.id if std else None),
                evidence_ids=list(dict.fromkeys(finding_evidence_ids)),
                # The model may interpret the observation, but it may not rewrite
                # what the consultant actually supplied. Clarification answers are
                # appended deterministically and remain visibly labelled.
                consultant_statement=(
                    "\n".join([
                        ob.text,
                        *[f"Clarification {index + 1}: {value}"
                          for index, value in enumerate(consultant_answers)],
                    ]) if consultant_answers else ob.text
                ),
                model_interpretation=f.model_interpretation,
                severity=severity, confidence=confidence,
                uncertainty_reasons=uncertainty,
                not_supported=f.not_supported,
                reasoning_trace=(
                    ([{"step": 0, "tool": "provider_execution", "actor": "SYSTEM",
                       "result": execution}] if execution["degraded"] else []) +
                    ([standard_snapshot] if standard_snapshot else []) +
                    (relevant_trace or inv["trace"])
                ),
                recurrence=recurrence,
                challenge_record=panel,
                recommended_action={
                    "description": f.recommended_action.description,
                    "owner_role": f.recommended_action.owner_role,
                    "suggested_due_date": due,
                    "verification_method": f.recommended_action.verification_method,
                })
            db.add(finding)
            checklist_reconciled = _reconcile_checklist_from_finding(
                db, audit, finding, std, ob, supporting_photos)
            ticket = (_create_field_ticket(
                db, audit, finding, ob, supporting_photos)
                if supporting_photos else None)
            created["findings"].append(finding.id)
            if ticket is not None:
                created.setdefault("tickets", []).append(ticket.id)
            if checklist_reconciled:
                created.setdefault("checklist_reconciled", []).append({
                    "finding_id": finding.id, "standard_code": std.code,
                    "zone_id": ob.zone_id,
                })
        else:
            created["no_issue"].append(d.observation_id)

    # Schema-valid output can still omit observations. Missing work is
    # uncertainty, never NO_ISSUE: make it visible and fail the audit closed.
    for ob in observations:
        if ob.id in processed:
            continue
        q = ClarificationQuestion(
            id=uid("q"), tenant_id=audit.tenant_id, audit_id=audit_id,
            observation_id=ob.id,
            question=("The analysis did not reach a supported determination. "
                      "What specific condition should be assessed?"),
            why_needed=("The model returned no decision for this observation; "
                        "the system fails closed."),
            options=[])
        db.add(q)
        created["clarifications"].append(q.id)
        created["demoted"].append(
            {"observation_id": ob.id, "reason": "model omitted observation decision"})

    db.flush()
    open_count = (db.query(ClarificationQuestion)
                    .filter_by(audit_id=audit_id, status="OPEN").count())
    # Analysis prepares a packet; only the explicit submit endpoint can hand the
    # visit to review. A model run must not silently finish an incomplete field
    # checklist merely because it has no open clarification at this moment.
    audit.status = "NEEDS_CLARIFICATION" if open_count else "COLLECTING"
    db.add(AuditLog(id=uid("log"), tenant_id=audit.tenant_id, actor="MODEL",
                    entity_type="audit", entity_id=audit_id, event="INVESTIGATE",
                    detail={"trace": inv["trace"], "steps": inv["steps"],
                            "stopped": inv["stopped"], "provider": inv.get("provider"),
                            "degraded": inv.get("degraded", False),
                            "retrieved_standards": sorted(ctx.retrieved_standard_codes)}))
    db.add(AuditLog(id=uid("log"), tenant_id=audit.tenant_id, actor="MODEL",
                    entity_type="audit", entity_id=audit_id, event="ANALYZE",
                     detail={"summary": result.overall_summary, **{k: v for k, v in created.items()}}))
    final_digest = _analysis_state_digest(db, audit)
    db.add(AuditLog(id=uid("log"), tenant_id=audit.tenant_id, actor="SYSTEM",
                    entity_type="audit", entity_id=audit_id,
                    event="ANALYSIS_STATE_COMPLETED",
                    detail={"state_digest": final_digest,
                            "provider_execution": execution}))
    db.commit()
    db.close()
    return {"summary": result.overall_summary, **created, "audit_status": audit.status}


def review_finding(finding_id: str, *, action: str, reviewer: str,
                   edits: dict | None = None, reason: str = "") -> dict:
    """Human review actions — the ONLY path to APPROVED (spec §17)."""
    db = SessionLocal()
    f = db.get(Finding, finding_id)
    if f is None:
        db.close()
        raise ValueError("finding not found")
    allowed = {"approve": "APPROVED", "reject": "REJECTED", "dispute": "DISPUTED",
               "request_evidence": "NEEDS_CLARIFICATION", "edit_approve": "APPROVED"}
    if action not in allowed:
        db.close()
        raise ValueError(f"unknown review action {action}")
    audit = db.get(AuditSession, f.audit_id)
    if audit and reviewer.strip().lower() == audit.consultant_name.strip().lower():
        db.close()
        raise ValueError(
            "separation of duty: the audit consultant cannot approve or decide their own finding")

    existing_action = db.query(Action).filter_by(finding_id=f.id).first()
    # Make repeated network submissions idempotent without allowing a terminal
    # human decision to be rewritten. This prevents duplicate corrective-action
    # tickets when a reviewer double-clicks or retries after a timeout.
    if f.status == "APPROVED" and action in {"approve", "edit_approve"}:
        out = {"finding_id": f.id, "status": f.status,
               "action_id": existing_action.id if existing_action else None,
               "idempotent": True}
        db.close()
        return out
    if f.status != "READY_FOR_REVIEW":
        prior = f.status
        db.close()
        raise ValueError(f"finding in terminal state {prior}; review transition rejected")

    prev = {"status": f.status, "severity": f.severity, "title": f.title}
    if action == "edit_approve" and edits:
        for k in ("title", "severity", "model_interpretation"):
            if k in edits:
                setattr(f, k, edits[k])
        if "recommended_action" in edits:
            f.recommended_action = {**f.recommended_action, **edits["recommended_action"]}
    f.status = allowed[action]
    entry = {"at": datetime.now(timezone.utc).isoformat(), "actor": reviewer,
             "action": action, "reason": reason, "previous": prev}
    f.review_history = [*f.review_history, entry]

    action_id = None
    field_ticket = next((ticket for ticket in db.query(OperationalTicket).filter_by(
        location_id=audit.location_id).all()
        if f.id in (ticket.source_refs or [])), None) if audit else None
    if f.status == "APPROVED" and existing_action is None:
        ra = f.recommended_action or {}
        a = Action(id=uid("act"), tenant_id=f.tenant_id, finding_id=f.id,
                   description=ra.get("description", ""), owner_role=ra.get("owner_role", ""),
                   due_date=ra.get("suggested_due_date", ""),
                   verification_method=ra.get("verification_method", ""),
                   events=[{"at": entry["at"], "event": "CREATED", "by": reviewer,
                            "operational_ticket_id": field_ticket.id if field_ticket else None}])
        db.add(a)
        action_id = a.id
        if field_ticket is not None:
            field_ticket.source_refs = list(dict.fromkeys([
                *(field_ticket.source_refs or []), a.id,
            ]))
            field_ticket.status = "OPEN"
            field_ticket.validity_status = "VALIDATED_BY_FINDING_REVIEW"
            field_ticket.events = [*(field_ticket.events or []), {
                "at": entry["at"], "event": "FINDING_REVIEW_VALIDATED",
                "by": reviewer, "finding_id": f.id, "action_id": a.id,
            }]
    elif field_ticket is not None and f.status == "REJECTED":
        field_ticket.status = "DISMISSED"
        field_ticket.validity_status = "NOT_SUBSTANTIATED"
        field_ticket.events = [*(field_ticket.events or []), {
            "at": entry["at"], "event": "FINDING_REVIEW_REJECTED",
            "by": reviewer, "finding_id": f.id, "note": reason,
        }]
    elif field_ticket is not None:
        field_ticket.events = [*(field_ticket.events or []), {
            "at": entry["at"], "event": "FINDING_REVIEW_UPDATE",
            "by": reviewer, "finding_id": f.id, "status": f.status,
            "note": reason,
        }]

    db.add(AuditLog(id=uid("log"), tenant_id=f.tenant_id, actor=reviewer,
                    entity_type="finding", entity_id=f.id, event=f"REVIEW_{action.upper()}",
                    detail=entry))
    db.commit()
    out = {"finding_id": f.id, "status": f.status, "action_id": action_id,
           "ticket_id": field_ticket.id if field_ticket else None}
    db.close()
    return out


def challenge_existing_finding(finding_id: str, reviewer: str) -> dict:
    """Run the expensive three-lens panel on demand in independent review."""
    db = SessionLocal()
    finding = db.get(Finding, finding_id)
    if finding is None:
        db.close()
        raise ValueError("finding not found")
    if (finding.challenge_record or {}).get("ran"):
        out = {**finding.challenge_record, "idempotent": True}
        db.close()
        return out
    if finding.status != "READY_FOR_REVIEW":
        prior = finding.status
        db.close()
        raise ValueError(f"finding in terminal state {prior}; challenge rejected")
    observation = db.get(Observation, finding.observation_id) if finding.observation_id else None
    standard = db.get(Standard, finding.standard_id) if finding.standard_id else None
    action = finding.recommended_action or {}
    draft = FindingDraft(
        standard_code=standard.code if standard else "UNSPECIFIED",
        lane=finding.lane, category=finding.category, title=finding.title,
        consultant_statement=finding.consultant_statement,
        model_interpretation=finding.model_interpretation,
        severity=finding.severity, confidence=finding.confidence,
        uncertainty_reasons=finding.uncertainty_reasons or ["Single visit"],
        not_supported=finding.not_supported or ["Conditions outside the captured evidence"],
        recommended_action=ActionDraft(
            description=action.get("description") or "Review and correct the observed condition.",
            owner_role=action.get("owner_role") or "Location Manager",
            due_in_days=7,
            verification_method=action.get("verification_method") or "After photo plus manager confirmation",
        ),
    )
    panel = challenge.run_panel(
        draft, observation_text=(observation.text if observation else finding.consultant_statement),
        standard=({"code": standard.code, "text": standard.text,
                   "category": standard.category} if standard else None),
        tenant_id=finding.tenant_id, audit_id=finding.audit_id, force=True,
    )
    challenge_notes = challenge.apply_outcome(draft, panel)
    effect: dict = {"finding_status": finding.status}
    if panel.get("outcome") == "DOWNGRADED":
        finding.severity = draft.severity
        finding.confidence = draft.confidence
        finding.uncertainty_reasons = list(dict.fromkeys([
            *(finding.uncertainty_reasons or []), *challenge_notes,
        ]))
        effect = {
            "finding_status": finding.status,
            "severity": finding.severity,
            "confidence": finding.confidence,
            "objections_persisted": challenge_notes,
        }
    elif panel.get("outcome") in {"OVERTURNED", "INCONCLUSIVE"}:
        finding.status = "NEEDS_CLARIFICATION"
        reason = (
            "The independent challenge panel overturned this candidate finding."
            if panel.get("outcome") == "OVERTURNED"
            else "The independent challenge panel could not reach a reliable decision."
        )
        objections = [
            f"Challenged ({item['lens']}): {item.get('specific_gap') or item.get('argument', '')}"
            for item in panel.get("challenges", [])
            if item.get("verdict") in {"OVERTURN", "WEAKEN", "ABSTAIN"}
        ]
        finding.uncertainty_reasons = list(dict.fromkeys([
            *(finding.uncertainty_reasons or []), reason, *objections,
        ]))
        audit = db.get(AuditSession, finding.audit_id)
        if audit is not None:
            audit.status = "NEEDS_CLARIFICATION"
        existing_question = (
            db.query(ClarificationQuestion)
            .filter_by(
                audit_id=finding.audit_id,
                observation_id=finding.observation_id,
                status="OPEN",
            )
            .first()
        )
        question_id = existing_question.id if existing_question else None
        if existing_question is None:
            settling = [value for value in panel.get("settling_evidence", []) if value]
            question = ClarificationQuestion(
                id=uid("q"), tenant_id=finding.tenant_id,
                audit_id=finding.audit_id,
                observation_id=finding.observation_id,
                question=(settling[0] if settling else
                          "What additional observable evidence would settle this assessment?"),
                why_needed=(
                    f"{reason} The candidate is halted and cannot be approved until "
                    "the evidence gap is resolved."
                ),
                options=[],
            )
            db.add(question)
            question_id = question.id
        effect = {
            "finding_status": finding.status,
            "clarification_question_id": question_id,
            "reason": reason,
        }
    finding.challenge_record = {
        **panel,
        "requested_by": reviewer,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "effect": effect,
    }
    db.add(AuditLog(id=uid("log"), tenant_id=finding.tenant_id, actor=reviewer,
                    entity_type="finding", entity_id=finding.id,
                    event="CHALLENGE_PANEL_RUN", detail=finding.challenge_record))
    db.commit()
    out = finding.challenge_record
    db.close()
    return out


def answer_clarification(question_id: str, answer: str) -> dict:
    db = SessionLocal()
    question = db.get(ClarificationQuestion, question_id)
    audit_id = question.audit_id if question is not None else None
    db.close()
    with audit_lock(audit_id):
        return _answer_clarification_unlocked(question_id, answer)


def _answer_clarification_unlocked(question_id: str, answer: str) -> dict:
    db = SessionLocal()
    q = db.get(ClarificationQuestion, question_id)
    if q is None:
        db.close()
        raise ValueError("question not found")
    if (q.why_needed or "").startswith(
            ("PHOTO_REQUIRED:", "UNMAPPED_PHOTO_REQUIRED:")):
        db.close()
        raise ValueError(
            "this evidence request can only be completed by an explicitly linked photo upload")
    answer = answer.strip()
    if re.search(r"\[[^\]]+\]|\{[^}]+\}|<[^>]+>", answer):
        db.close()
        raise ValueError("replace every placeholder with the observed detail before answering")
    if q.status == "ANSWERED":
        if (q.answer or "").strip() == answer:
            out = {"id": q.id, "audit_id": q.audit_id,
                   "status": q.status, "idempotent": True}
            db.close()
            return out
        db.close()
        raise ValueError("question was already answered; answers are immutable")
    q.answer = answer
    q.status = "ANSWERED"
    db.add(AuditLog(id=uid("log"), tenant_id=q.tenant_id, actor="CONSULTANT",
                    entity_type="clarification", entity_id=q.id, event="ANSWERED",
                    detail={"answer": answer}))
    db.commit()
    audit_id = q.audit_id
    db.close()
    return analyze_audit(audit_id)  # re-run with the complete clarification history
