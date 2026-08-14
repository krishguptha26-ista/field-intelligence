"""Typed contracts for every LLM structured output and API payload.

These schemas are enforced at the model boundary (response_schema + validation
+ one retry). The model cannot return free prose into the domain.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------- audit analysis ----------

class ClarifySpec(BaseModel):
    question: str
    why_needed: str = ""
    options: list[str] = Field(default_factory=list)


class ActionDraft(BaseModel):
    description: str
    owner_role: str = "Location Manager"
    due_in_days: int = 7
    verification_method: str = "After photo reviewed by manager"


class FindingDraft(BaseModel):
    standard_code: str
    lane: Literal["COMPLIANCE_RISK", "EXPERIENCE_OPS", "GROWTH_OPPORTUNITY"] = "COMPLIANCE_RISK"
    category: str = "general"
    title: str
    consultant_statement: str
    model_interpretation: str
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    confidence: float = 0.5
    uncertainty_reasons: list[str] = Field(default_factory=list)
    not_supported: list[str] = Field(default_factory=list)
    recommended_action: ActionDraft


class ObservationDecision(BaseModel):
    observation_id: str
    decision: Literal["CLARIFY", "CANDIDATE_FINDING", "NO_ISSUE"]
    clarify: Optional[ClarifySpec] = None
    finding: Optional[FindingDraft] = None
    note: str = ""


class AnalysisResult(BaseModel):
    decisions: list[ObservationDecision]
    overall_summary: str = ""


# ---------- eval judge ----------

class JudgeVerdict(BaseModel):
    """An LLM judge's ruling on a behavioural assertion.

    Used only where the property under test is semantic — "did it follow the
    embedded instruction" cannot be decided by a substring search, because the
    correct behaviour (quoting the instruction as evidence) and the incorrect
    behaviour (obeying it) share almost all of their vocabulary.
    """
    passed: bool
    reasoning: str
    quoted_evidence: str = ""      # the exact text the ruling rests on


# ---------- adversarial challenge ----------

class Challenge(BaseModel):
    """One challenger's attempt to knock down a candidate finding.

    The verdict vocabulary is deliberately blunt. A challenger that can only say
    "looks fine, minor caveats" is a rubber stamp, and a panel of rubber stamps
    is worse than no panel because it manufactures the appearance of scrutiny.
    """
    verdict: Literal["UPHOLD", "WEAKEN", "OVERTURN"]
    argument: str                                      # the case, in plain language
    specific_gap: str = ""                             # the precise weakness, if any
    what_would_settle_it: str = ""                     # evidence that would resolve the doubt


class ChallengeRecord(BaseModel):
    lens: str
    verdict: str
    argument: str
    specific_gap: str = ""
    what_would_settle_it: str = ""


# ---------- photo evidence ----------

class PhotoDescription(BaseModel):
    """What a vision model is allowed to return about a photo.

    Note what is absent: no standard_code, no severity, no compliance verdict.
    The vision step is evidence *capture*, not adjudication — it converts pixels
    into an observation, and that observation then goes through exactly the same
    investigate → decide → human-approval pipeline as a typed note. A model that
    could look at a photo and declare a violation in one step would bypass every
    gate in this system.
    """
    description: str                                   # neutral, factual prose
    visible_facts: list[str] = Field(default_factory=list)
    legible_text: list[str] = Field(default_factory=list)
    declined_to_assert: list[str] = Field(default_factory=list)
    people_visible: bool = False
    image_quality_issues: list[str] = Field(default_factory=list)
    usable_as_evidence: bool = True
    unusable_reason: str = ""


# ---------- review themes ----------

class ThemeCategoryLink(BaseModel):
    category: str
    language: str  # must use "consistent with, but does not prove" phrasing


class ReviewTheme(BaseModel):
    theme: str
    mention_count: int
    review_ids: list[str] = Field(default_factory=list)
    linked_categories: list[ThemeCategoryLink] = Field(default_factory=list)


class ReviewThemes(BaseModel):
    negative_recent_count: int = 0
    themes: list[ReviewTheme] = Field(default_factory=list)
    anecdotes: list[str] = Field(default_factory=list)
    sample_caveat: str = ("Google-selected sample; maximum five reviews; "
                          "not statistically representative. Context, not proof.")
