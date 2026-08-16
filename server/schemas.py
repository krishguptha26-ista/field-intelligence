"""Typed contracts for every LLM structured output and API payload.

These schemas are enforced at the model boundary (response_schema + validation
+ one retry). The model cannot return free prose into the domain.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------- audit analysis ----------

class ClarifySpec(BaseModel):
    question: str = Field(min_length=3, max_length=1200)
    why_needed: str = Field(default="", max_length=1200)
    options: list[str] = Field(default_factory=list, max_length=12)


class ActionDraft(BaseModel):
    description: str = Field(min_length=3, max_length=2000)
    owner_role: str = Field(default="Location Manager", min_length=2, max_length=120)
    due_in_days: int = Field(default=7, ge=0, le=365)
    verification_method: str = Field(
        default="After photo reviewed by manager", min_length=3, max_length=1000)


class FindingDraft(BaseModel):
    standard_code: str = Field(min_length=1, max_length=80)
    lane: Literal["COMPLIANCE_RISK", "EXPERIENCE_OPS", "GROWTH_OPPORTUNITY"] = "COMPLIANCE_RISK"
    category: str = Field(default="general", min_length=1, max_length=120)
    title: str = Field(min_length=3, max_length=240)
    consultant_statement: str = Field(min_length=1, max_length=10000)
    model_interpretation: str = Field(min_length=3, max_length=5000)
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainty_reasons: list[str] = Field(default_factory=list, max_length=30)
    not_supported: list[str] = Field(default_factory=list, max_length=30)
    recommended_action: ActionDraft


class ObservationDecision(BaseModel):
    observation_id: str = Field(min_length=1, max_length=100)
    decision: Literal["CLARIFY", "CANDIDATE_FINDING", "NO_ISSUE"]
    clarify: Optional[ClarifySpec] = None
    finding: Optional[FindingDraft] = None
    note: str = ""


class AnalysisResult(BaseModel):
    decisions: list[ObservationDecision] = Field(max_length=500)
    overall_summary: str = Field(default="", max_length=5000)


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
    argument: str = Field(min_length=3, max_length=3000)  # the case, in plain language
    specific_gap: str = Field(default="", max_length=1500)
    what_would_settle_it: str = Field(default="", max_length=1500)


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
    matches_requested_context: bool = True
    mismatch_reason: str = ""


class MediaDescription(BaseModel):
    """Neutral extraction contract for consultant audio and short video.

    Like ``PhotoDescription``, this contract deliberately has no standard,
    severity or compliance-verdict field. Audio is a transcription of a human
    claim; video can add visible facts. Both still enter the normal
    investigate -> decide -> human-review pipeline.
    """
    transcript: str = Field(default="", max_length=20000)
    description: str = Field(min_length=1, max_length=10000)
    observable_facts: list[str] = Field(default_factory=list, max_length=100)
    timecoded_facts: list[str] = Field(default_factory=list, max_length=100)
    declined_to_assert: list[str] = Field(default_factory=list, max_length=100)
    people_visible: bool = False
    quality_issues: list[str] = Field(default_factory=list, max_length=100)
    usable_as_evidence: bool = True
    unusable_reason: str = ""
    matches_requested_context: bool = True
    mismatch_reason: str = ""


# ---------- review themes ----------

class ThemeCategoryLink(BaseModel):
    category: str
    language: str  # must use "consistent with, but does not prove" phrasing


class ReviewTheme(BaseModel):
    theme: str = Field(min_length=2, max_length=240)
    mention_count: int = Field(ge=1)
    review_ids: list[str] = Field(default_factory=list, max_length=1000)
    linked_categories: list[ThemeCategoryLink] = Field(default_factory=list, max_length=30)


class ReviewThemes(BaseModel):
    negative_recent_count: int = Field(default=0, ge=0)
    themes: list[ReviewTheme] = Field(default_factory=list)
    anecdotes: list[str] = Field(default_factory=list)
    sample_caveat: str = ("Google-selected sample; maximum five reviews; "
                          "not statistically representative. Context, not proof.")
