"""Principal orchestrator (spec §15): one brain, deterministic tools,
explicit state machine. The model proposes; policy re-checks; humans approve.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from .. import config
from ..gateway import get_provider
from ..models import (Action, AuditLog, AuditSession, ClarificationQuestion,
                      EvidenceItem, Finding, Observation, SessionLocal,
                      Standard, uid)
from ..schemas import AnalysisResult
from . import challenge
from . import tools as toolkit

VAGUE_BLOCKLIST = ("a little", "kinda", "kind of", "somewhat", "seemed", "maybe")

MAX_TOOL_STEPS = 6

_SEVERITY_LADDER = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


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


def analyze_audit(audit_id: str) -> dict:
    """Run the full analysis pass over an audit's observations."""
    db = SessionLocal()
    audit = db.get(AuditSession, audit_id)
    if audit is None:
        db.close()
        raise ValueError("audit not found")

    calls = db.query(AuditLog).filter_by(entity_id=audit_id, event="ANALYZE").count()
    if calls >= config.MAX_LLM_CALLS_PER_AUDIT:
        db.close()
        raise RuntimeError("per-audit analysis budget exceeded — acknowledge in UI to continue")

    observations = db.query(Observation).filter_by(audit_id=audit_id).all()
    standards = db.query(Standard).filter_by(tenant_id=audit.tenant_id, active=True).all()
    open_qs = {q.observation_id: q for q in
               db.query(ClarificationQuestion).filter_by(audit_id=audit_id).all()}

    # Idempotency: an observation that already produced a finding is settled.
    # Re-analysis must never duplicate it (and we save the tokens too).
    settled = {f.observation_id for f in db.query(Finding).filter_by(audit_id=audit_id).all()
               if f.observation_id}
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
             "clarification_answer": (open_qs[o.id].answer if o.id in open_qs and open_qs[o.id].answer else None)}
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

    std_by_code = {s.code: s for s in standards}
    valid_codes = set(std_by_code)
    created: dict = {"findings": [], "clarifications": [], "no_issue": [], "demoted": [],
                     "tool_calls": len(inv["trace"]), "investigation_stopped": inv["stopped"]}

    for d in result.decisions:
        ob = db.get(Observation, d.observation_id)
        if ob is None:
            continue
        # The evidence for an observation is what the consultant wrote PLUS any
        # clarification they have since answered. Every downstream check has to
        # see the whole of it: judging the merged evidence against the original
        # vague wording alone is how a question that was properly answered gets
        # thrown away for being vague.
        answered = open_qs.get(ob.id).answer if open_qs.get(ob.id) else None
        evidence_text = f"{ob.text} {answered}".strip() if answered else ob.text
        ok, reason = _policy_check(d, evidence_text)
        if ok:
            ok, reason = _grounding_check(d, ctx, valid_codes)
        if not ok and d.decision == "CANDIDATE_FINDING":
            created["demoted"].append({"observation_id": ob.id, "reason": reason})
            d.decision = "CLARIFY"
            if d.clarify is None:  # demotion must still leave the reviewer something to act on
                from ..schemas import ClarifySpec
                d.clarify = ClarifySpec(
                    question=("The evidence as written could not be tied to a verified "
                              "standard for this location. What exactly was observed, "
                              "and in which zone?"),
                    why_needed=f"Automatic policy demotion: {reason}")

        if d.decision == "CLARIFY":
            existing = open_qs.get(ob.id)
            if existing and existing.answer:
                pass  # already answered; model still unsure → keep as open info
            elif not existing:
                q = ClarificationQuestion(
                    id=uid("q"), tenant_id=audit.tenant_id, audit_id=audit_id,
                    observation_id=ob.id,
                    question=(d.clarify.question if d.clarify else "Please provide specifics."),
                    why_needed=(d.clarify.why_needed if d.clarify else ""),
                    options=(d.clarify.options if d.clarify else []))
                db.add(q)
                created["clarifications"].append(q.id)
        elif d.decision == "CANDIDATE_FINDING" and d.finding:
            f = d.finding
            std = std_by_code.get(f.standard_code)
            ev = EvidenceItem(
                id=uid("ev"), tenant_id=audit.tenant_id, location_id=audit.location_id,
                source_type="OBSERVATION", collection_method="UPLOAD",
                provenance="UPLOADED_DOCUMENT", trust_class="OFFICIAL_OWNED",
                excerpt=ob.text, observed_at=ob.created_at,
                payload={"observation_id": ob.id, "kind": ob.kind})
            db.add(ev)

            # ---- adversarial challenge, before any human sees this ----
            panel = challenge.run_panel(
                f, observation_text=evidence_text,
                standard=({"code": std.code, "text": std.text, "category": std.category}
                          if std else None),
                tenant_id=audit.tenant_id, audit_id=audit_id)
            if panel.get("outcome") == "OVERTURNED":
                # The panel killed it. The reviewer gets the question the
                # challengers said would settle it, not a finding they would
                # have had to reject themselves.
                created["demoted"].append(
                    {"observation_id": ob.id,
                     "reason": "overturned by challenge panel",
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
                    {"observation_id": ob.id, "outcome": "OVERTURNED", **panel["votes"]})
                continue

            challenge_notes = challenge.apply_outcome(f, panel)
            created.setdefault("challenge_outcomes", []).append(
                {"observation_id": ob.id, "outcome": panel.get("outcome"),
                 **panel.get("votes", {})})

            category = std.category if std else f.category
            if _signal_language_check(f):
                created.setdefault("policy_annotations", []).append(
                    {"observation_id": ob.id, "rule": "customer_signal_non_causal_language"})
            recurrence = _detect_recurrence(db, audit, ob, category)
            severity = f.severity
            uncertainty = list(f.uncertainty_reasons) + challenge_notes
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
            finding = Finding(
                id=uid("finding"), tenant_id=audit.tenant_id, audit_id=audit_id,
                observation_id=ob.id,
                lane=f.lane, category=f.category, title=f.title,
                status="READY_FOR_REVIEW",
                standard_id=(std.id if std else None),
                evidence_ids=[ev.id],
                consultant_statement=f.consultant_statement,
                model_interpretation=f.model_interpretation,
                severity=severity, confidence=f.confidence,
                uncertainty_reasons=uncertainty,
                not_supported=f.not_supported,
                reasoning_trace=(relevant_trace or inv["trace"]),
                recurrence=recurrence,
                challenge_record=panel,
                recommended_action={
                    "description": f.recommended_action.description,
                    "owner_role": f.recommended_action.owner_role,
                    "suggested_due_date": due,
                    "verification_method": f.recommended_action.verification_method,
                })
            db.add(finding)
            created["findings"].append(finding.id)
        else:
            created["no_issue"].append(d.observation_id)

    audit.status = ("NEEDS_CLARIFICATION" if created["clarifications"] else "READY_FOR_REVIEW")
    db.add(AuditLog(id=uid("log"), tenant_id=audit.tenant_id, actor="MODEL",
                    entity_type="audit", entity_id=audit_id, event="INVESTIGATE",
                    detail={"trace": inv["trace"], "steps": inv["steps"],
                            "stopped": inv["stopped"], "provider": inv.get("provider"),
                            "degraded": inv.get("degraded", False),
                            "retrieved_standards": sorted(ctx.retrieved_standard_codes)}))
    db.add(AuditLog(id=uid("log"), tenant_id=audit.tenant_id, actor="MODEL",
                    entity_type="audit", entity_id=audit_id, event="ANALYZE",
                    detail={"summary": result.overall_summary, **{k: v for k, v in created.items()}}))
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
    if f.status == "APPROVED":
        ra = f.recommended_action or {}
        a = Action(id=uid("act"), tenant_id=f.tenant_id, finding_id=f.id,
                   description=ra.get("description", ""), owner_role=ra.get("owner_role", ""),
                   due_date=ra.get("suggested_due_date", ""),
                   verification_method=ra.get("verification_method", ""),
                   events=[{"at": entry["at"], "event": "CREATED", "by": reviewer}])
        db.add(a)
        action_id = a.id

    db.add(AuditLog(id=uid("log"), tenant_id=f.tenant_id, actor=reviewer,
                    entity_type="finding", entity_id=f.id, event=f"REVIEW_{action.upper()}",
                    detail=entry))
    db.commit()
    out = {"finding_id": f.id, "status": f.status, "action_id": action_id}
    db.close()
    return out


def answer_clarification(question_id: str, answer: str) -> dict:
    db = SessionLocal()
    q = db.get(ClarificationQuestion, question_id)
    if q is None:
        db.close()
        raise ValueError("question not found")
    q.answer = answer
    q.status = "ANSWERED"
    db.add(AuditLog(id=uid("log"), tenant_id=q.tenant_id, actor="CONSULTANT",
                    entity_type="clarification", entity_id=q.id, event="ANSWERED",
                    detail={"answer": answer}))
    db.commit()
    audit_id = q.audit_id
    db.close()
    return analyze_audit(audit_id)  # re-run with new information
