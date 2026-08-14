"""Adversarial challenge panel — three specialists attack every candidate finding.

The product's stated personality is that it is hard to convince. Until now that
was enforced by one prompt and one policy layer, both of which sit on the same
side of the argument as the model that produced the finding. This adds an
opposing side.

Three challengers with different briefs examine each candidate finding in
parallel and independently. None of them sees the others' arguments, because
challengers that can read each other converge, and three converged opinions are
one opinion wearing a panel's clothing.

Adjudication is deterministic Python, not a fourth model:

    2+ OVERTURN            → the finding never reaches a human; it becomes a
                             clarifying question built from the challengers' own
                             "what would settle it" answers
    1 OVERTURN or 2+ WEAKEN → survives, but downgraded: severity down one level,
                             confidence reduced, and every challenger's objection
                             recorded on the finding
    otherwise               → upheld, with the challenge record attached

Asking an LLM to referee LLMs adds a failure mode and removes an auditable one.
A vote count is something a reviewer can check in four seconds.

Cost: three extra calls per candidate finding. On the measured POC numbers that
is roughly a tenth of a US cent, against the cost of one consultant-hour spent
defending a finding that should never have been written.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from .. import config
from ..gateway import get_provider
from ..schemas import Challenge

# The lenses. Three, not five: past three the arguments start repeating, and
# every extra lens is another call on the critical path of a demo.
LENSES = [
    ("evidence_sufficiency",
     "You are an evidence critic. Ask only one question: does what was actually "
     "observed support what is being claimed, or is there an inferential leap "
     "between them? Watch for a single moment being described as an ongoing "
     "condition, one instance being generalised to a place or a process, and "
     "root cause being asserted where only a symptom was seen."),
    ("franchisee_advocate",
     "You represent the operator of this location, in good faith. Make the "
     "strongest honest case that this finding is unfair, premature, or would "
     "damage trust disproportionately to what it establishes. Consider: was this "
     "outside their control, already being handled, normal mid-service state, or "
     "a snapshot of a moment rather than a standard of operation? You are not "
     "defending negligence — you are insisting the accusation be earned."),
    ("standards_fit",
     "You are a standards specialist. Ignore whether something is wrong; ask "
     "whether the CITED standard is the right one and whether the observation "
     "falls inside its actual scope. A real condition cited against a "
     "near-miss standard is still a defective finding, and it is the kind an "
     "operator will successfully dispute."),
]

_SEVERITY = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _downgrade(sev: str) -> str:
    i = _SEVERITY.index(sev) if sev in _SEVERITY else 2
    return _SEVERITY[max(i - 1, 0)]


def _brief(finding, observation_text: str, standard: dict | None) -> str:
    return json.dumps({
        "consultant_observed": observation_text,
        "proposed_title": finding.title,
        "cited_standard": standard or {"code": finding.standard_code,
                                       "text": "(standard text unavailable)"},
        "model_interpretation": finding.model_interpretation,
        "proposed_severity": finding.severity,
        "model_confidence": finding.confidence,
        "model_stated_uncertainty": finding.uncertainty_reasons,
        "model_says_not_supported": finding.not_supported,
        "proposed_action": finding.recommended_action.model_dump(),
    }, indent=2)


def run_panel(finding, *, observation_text: str, standard: dict | None,
              tenant_id: str, audit_id: str | None) -> dict:
    """Run all three challengers concurrently and adjudicate deterministically."""
    if not config.ENABLE_CHALLENGE_PANEL:
        return {"ran": False, "reason": "challenge panel disabled by configuration",
                "challenges": [], "outcome": "NOT_RUN"}

    doc = (config.PROMPTS_DIR / "challenge_panel.md").read_text()
    brief = _brief(finding, observation_text, standard)
    provider = get_provider()

    def one(lens_name: str, lens_brief: str) -> dict:
        prompt = (doc.replace("{LENS_BRIEF}", lens_brief)
                  + f"\n\nCANDIDATE_FINDING:\n{brief}\n\nINPUT_JSON:{brief}")
        try:
            c: Challenge = provider.generate(
                purpose=f"challenge:{lens_name}", prompt=prompt, schema=Challenge,
                tenant_id=tenant_id, audit_id=audit_id)
            return {"lens": lens_name, "verdict": c.verdict, "argument": c.argument,
                    "specific_gap": c.specific_gap,
                    "what_would_settle_it": c.what_would_settle_it}
        except Exception as e:
            # A challenger that fails must not silently become an UPHOLD. It is
            # recorded as ABSTAIN and counts toward nothing.
            return {"lens": lens_name, "verdict": "ABSTAIN",
                    "argument": f"Challenger unavailable: {type(e).__name__}",
                    "specific_gap": "", "what_would_settle_it": ""}

    with ThreadPoolExecutor(max_workers=len(LENSES)) as pool:
        challenges = list(pool.map(lambda p: one(*p), LENSES))

    overturn = [c for c in challenges if c["verdict"] == "OVERTURN"]
    weaken = [c for c in challenges if c["verdict"] == "WEAKEN"]
    abstain = [c for c in challenges if c["verdict"] == "ABSTAIN"]

    if len(overturn) >= 2:
        outcome = "OVERTURNED"
    elif overturn or len(weaken) >= 2:
        outcome = "DOWNGRADED"
    else:
        outcome = "UPHELD"

    return {"ran": True, "outcome": outcome, "challenges": challenges,
            "votes": {"overturn": len(overturn), "weaken": len(weaken),
                      "uphold": len(challenges) - len(overturn) - len(weaken) - len(abstain),
                      "abstain": len(abstain)},
            "settling_evidence": [c["what_would_settle_it"] for c in challenges
                                  if c.get("what_would_settle_it")]}


def apply_outcome(finding, panel: dict) -> list[str]:
    """Fold a DOWNGRADED verdict into the finding. Returns added uncertainty notes.

    The objection is written onto the finding in the challenger's own words
    rather than summarised away, so a reviewer sees the actual argument that was
    made against it and can disagree with the panel as easily as with the model.
    """
    notes: list[str] = []
    if panel.get("outcome") != "DOWNGRADED":
        return notes
    before = finding.severity
    finding.severity = _downgrade(finding.severity)
    finding.confidence = round(max(finding.confidence - 0.15, 0.05), 2)
    if finding.severity != before:
        notes.append(f"Severity reduced {before} → {finding.severity} by the challenge panel.")
    for c in panel["challenges"]:
        if c["verdict"] in ("WEAKEN", "OVERTURN") and c.get("specific_gap"):
            notes.append(f"Challenged ({c['lens']}): {c['specific_gap']}")
    return notes
