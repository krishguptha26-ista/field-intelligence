"""The agent's tool surface — narrow, typed, and READ-ONLY by construction.

Design position (ADR-009): a governed agent is not one that is trusted with
powerful tools; it is one whose tools cannot do damage. Three rules hold here:

1. **Every tool is read-only.** Nothing in this module writes to the database.
   All mutation happens in deterministic Python in `orchestrator.py` *after*
   the model has returned a schema-validated decision. The model can look, and
   propose. It can never change state, and it never touches a browser, a shell,
   or the filesystem.

2. **Tenant scope is injected, never argued.** `tenant_id` / `location_id` come
   from the server-side `ToolContext`, not from model-supplied arguments. A
   model that hallucinated another tenant's id would still only ever read its
   own tenant's rows.

3. **Tool results carry their own provenance.** Customer-signal results arrive
   stamped CONTEXT_ONLY, so the model cannot receive public sentiment in a form
   that looks like evidence. The label travels with the data, not with the
   prompt that asked for it.

The tool call log this module produces is the audit trail for *how* the agent
reached a conclusion, not just what it concluded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..models import (Action, AuditSession, Finding, Location, SessionLocal,
                      Standard, Zone)

# Words that carry no discriminating power when matching a note to a standard.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "is", "was",
    "were", "be", "been", "it", "its", "this", "that", "there", "with", "for",
    "from", "by", "as", "but", "not", "no", "had", "has", "have", "looked",
    "seemed", "very", "some", "any", "near", "around", "after", "before",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", (text or "").lower())
            if len(t) > 2 and t not in _STOPWORDS}


@dataclass
class ToolContext:
    """Server-controlled scope for a single analysis run.

    The model never supplies these values; they are bound before the loop
    starts and cannot be overridden by anything the model emits.
    """
    tenant_id: str
    location_id: str
    audit_id: str
    # Codes returned by search_standards during this run. The citation
    # grounding check in the orchestrator reads this: a finding may only cite
    # a standard the agent actually retrieved.
    retrieved_standard_codes: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _search_standards(args: dict, ctx: ToolContext) -> dict:
    """Retrieve candidate standards by relevance instead of dumping the corpus.

    Twelve demo standards would fit in a prompt; a real brand manual is
    hundreds of clauses across dozens of tenants, and that is the case this
    has to be built for. Scoring is deliberately deterministic (lexical
    overlap + category match) so the same note retrieves the same clauses on
    every run — an embedding index is the production swap, recorded in
    ADR-009, but it would make evals non-reproducible for no POC benefit.
    """
    query = str(args.get("query", ""))
    category = args.get("category") or None
    limit = min(int(args.get("limit", 5) or 5), 8)

    db = SessionLocal()
    rows = db.query(Standard).filter_by(tenant_id=ctx.tenant_id, active=True).all()
    db.close()

    q = _tokens(query)
    scored: list[tuple[float, Standard]] = []
    for s in rows:
        overlap = len(q & _tokens(s.text))
        score = float(overlap)
        if category and s.category == category:
            score += 2.5
        if overlap and s.category.replace("_", " ") in query.lower():
            score += 1.0
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda p: (-p[0], p[1].code))
    top = scored[:limit]

    # If nothing scored, return the tenant's category list rather than a guess.
    if not top:
        cats = sorted({s.category for s in rows})
        return {"matches": [], "no_match": True,
                "available_categories": cats,
                "guidance": ("No standard matched this wording. Do not invent one. "
                             "If the observation is too vague to match, ask a "
                             "clarifying question instead.")}

    for _, s in top:
        ctx.retrieved_standard_codes.add(s.code)
    return {"matches": [{"code": s.code, "category": s.category, "text": s.text,
                         "severity_default": s.severity_default,
                         "source_label": s.source_label,
                         "relevance": round(score, 2)}
                        for score, s in top],
            "note": ("You may only cite a standard code that appears in a "
                     "search_standards result. Citations are checked.")}


def _location_history(args: dict, ctx: ToolContext) -> dict:
    """What this location has been found for before, and whether it was fixed.

    This is the tool that makes the agent worth more than a checklist app. A
    condition appearing for the first time is an incident; the same condition
    reappearing ninety days after a manager signed it off is a process failure,
    and those are different conversations with a franchisee.
    """
    category = args.get("category") or None
    days = min(int(args.get("days", 540) or 540), 1095)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    db = SessionLocal()
    audit_ids = [a.id for a in db.query(AuditSession)
                 .filter_by(location_id=ctx.location_id).all()
                 if a.id != ctx.audit_id]
    prior: list[Finding] = []
    if audit_ids:
        q = db.query(Finding).filter(Finding.audit_id.in_(audit_ids))
        if category:
            q = q.filter(Finding.category == category)
        prior = [f for f in q.all()
                 if f.created_at and _aware(f.created_at) >= cutoff
                 and f.status in ("APPROVED", "DISPUTED")]
    actions = {a.finding_id: a for a in db.query(Action)
               .filter(Action.finding_id.in_([f.id for f in prior])).all()} if prior else {}
    codes = {s.id: s.code for s in db.query(Standard).filter_by(tenant_id=ctx.tenant_id).all()}
    db.close()

    out = []
    now = datetime.now(timezone.utc)
    for f in sorted(prior, key=lambda x: _aware(x.created_at), reverse=True)[:10]:
        act = actions.get(f.id)
        closed = act.status == "VERIFIED" if act else False
        out.append({
            "finding_id": f.id, "title": f.title, "category": f.category,
            "standard_code_cited": codes.get(f.standard_id),
            "severity": f.severity, "status": f.status,
            "days_ago": (now - _aware(f.created_at)).days,
            "corrective_action": (act.description if act else None),
            "action_status": (act.status if act else "NONE"),
            "was_verified_closed": closed,
        })
    return {"prior_findings": out, "count": len(out),
            "guidance": ("A prior VERIFIED-closed finding in the same category that "
                         "matches the current observation is a RECURRENCE. Say so in "
                         "model_interpretation and raise severity one level, but the "
                         "current observation must still stand on its own evidence — "
                         "history is context, not a substitute for what was seen today.")}


def _zone_context(args: dict, ctx: ToolContext) -> dict:
    """Zone metadata, including the privacy level that governs photo handling."""
    zone_id = args.get("zone_id") or None
    db = SessionLocal()
    zones = db.query(Zone).filter_by(location_id=ctx.location_id).all()
    db.close()
    if zone_id:
        z = next((z for z in zones if z.id == zone_id), None)
        if z is None:
            return {"found": False, "known_zones": [{"id": z.id, "name": z.name} for z in zones]}
        return {"found": True, "id": z.id, "name": z.name, "required": z.required,
                "privacy_level": z.privacy_level}
    return {"zones": [{"id": z.id, "name": z.name, "required": z.required,
                       "privacy_level": z.privacy_level} for z in zones]}


def _customer_signal_context(args: dict, ctx: ToolContext) -> dict:
    """Public review themes — returned pre-labelled as context that is not proof.

    The label is part of the payload rather than part of the prompt. A prompt
    instruction can be crowded out by a long transcript; a field named
    `usage_restriction` sitting next to the data cannot.
    """
    from ..connectors.places import fetch_review_sample, theme_summary_cached
    category = args.get("category") or None
    sample = fetch_review_sample(ctx.location_id)
    themes = theme_summary_cached(ctx.location_id, ctx.tenant_id)
    picked = themes.get("themes", [])
    if category:
        picked = [t for t in picked
                  if any(l.get("category") == category for l in t.get("linked_categories", []))]
    return {
        "usage_restriction": ("CONTEXT_ONLY — public sentiment may NOT be cited as "
                              "evidence for a finding and may NOT create one. It may "
                              "only corroborate a finding that already stands on "
                              "field evidence, using 'consistent with, but does not "
                              "prove' language."),
        "provenance": sample.get("provenance"),
        "sample_caveat": sample.get("sample_caveat"),
        "sample_size": len(sample.get("reviews", [])),
        "window_days": sample.get("window_days"),
        "themes": picked,
        "anecdotes": themes.get("anecdotes", []),
    }


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    fn: Callable[[dict, ToolContext], dict]


REGISTRY: list[ToolSpec] = [
    ToolSpec(
        name="search_standards",
        description=("Retrieve the brand/operational standards most relevant to an "
                     "observation. Call this before proposing any finding: you may only "
                     "cite a standard code returned by this tool."),
        parameters={"type": "object", "properties": {
            "query": {"type": "string",
                      "description": "The observation wording, or the condition described in it."},
            "category": {"type": "string",
                         "description": "Optional category filter, e.g. cleanliness, safety, operations."},
            "limit": {"type": "integer", "description": "Max results, default 5."}},
            "required": ["query"]},
        fn=_search_standards),
    ToolSpec(
        name="location_history",
        description=("Prior approved findings at THIS location and whether their corrective "
                     "actions were verified closed. Use it to detect a recurring issue."),
        parameters={"type": "object", "properties": {
            "category": {"type": "string", "description": "Optional category filter."},
            "days": {"type": "integer", "description": "Look-back window in days, default 540."}}},
        fn=_location_history),
    ToolSpec(
        name="zone_context",
        description=("Zone metadata for this location, including privacy_level which governs "
                     "how photo evidence from that zone may be handled."),
        parameters={"type": "object", "properties": {
            "zone_id": {"type": "string", "description": "Optional; omit to list all zones."}}},
        fn=_zone_context),
    ToolSpec(
        name="customer_signal_context",
        description=("Recurring themes in recent public reviews. Returns CONTEXT_ONLY data: "
                     "it can corroborate a finding that already stands on field evidence, and "
                     "can never create or prove one."),
        parameters={"type": "object", "properties": {
            "category": {"type": "string", "description": "Optional category filter."}}},
        fn=_customer_signal_context),
]

_BY_NAME = {t.name: t for t in REGISTRY}


def execute(name: str, args: dict, ctx: ToolContext) -> dict:
    """Dispatch a model-requested tool call. Unknown names are refused, not guessed."""
    spec = _BY_NAME.get(name)
    if spec is None:
        return {"error": f"unknown tool '{name}'",
                "available": sorted(_BY_NAME),
                "guidance": "Call only the tools listed. Do not invent tool names."}
    try:
        return spec.fn(args or {}, ctx)
    except Exception as e:  # a tool fault must not kill the analysis
        return {"error": f"{type(e).__name__}: {str(e)[:160]}",
                "guidance": "This tool failed. Continue with the evidence you have, "
                            "and state the gap in uncertainty_reasons."}


def declarations() -> list[dict]:
    """Provider-neutral tool declarations (OpenAI/Gemini both accept this shape)."""
    return [{"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in REGISTRY]
