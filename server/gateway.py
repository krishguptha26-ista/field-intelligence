"""Model gateway: one interface, multiple providers (spec §5, §20.4).

- GeminiProvider: live structured output via google-genai, schema-enforced.
- FixtureProvider: deterministic policy engine so the FULL demo and eval suite
  run with zero keys/network. Clearly labelled in every response.

Every call is recorded in the model_calls cost ledger. Prices come from
data/fixtures/pricing.json (config, not code).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Type, TypeVar

from pydantic import BaseModel

from . import config
from .budget import ModelBudgetExceeded, require_model_budget
from .locks import model_workflow_lock
from .models import ModelCall, SessionLocal, uid
from .schemas import (ActionDraft, AnalysisResult, Challenge, ClarifySpec,
                      FindingDraft, ObservationDecision, ReviewTheme,
                      ReviewThemes, ThemeCategoryLink)

T = TypeVar("T", bound=BaseModel)

_PRICING = json.loads((config.FIXTURES_DIR / "pricing.json").read_text())


def _system_prompt() -> str:
    return (config.PROMPTS_DIR / "system.md").read_text()


def _extract_payload(prompt: str) -> dict:
    """Pull the INPUT_JSON object out of a prompt, ignoring anything after it.

    The fixture provider needs the structured payload, and prompts are not
    JSON — they are documents with a JSON block embedded in them, and other
    sections (INVESTIGATION_RESULTS, CANDIDATE_FINDING) may follow it.
    `json.loads` on everything after the marker therefore fails with
    "Extra data", which is precisely what it did the first time a two-phase
    prompt met the keyless path.

    `raw_decode` reads exactly one JSON value and stops, so section order in
    the prompt no longer matters.
    """
    if "INPUT_JSON:" not in prompt:
        return {}
    # rsplit, not split: prompt documents describe their own sections by name,
    # so the marker occurs as prose before it occurs as a delimiter. Taking the
    # first hit parses the documentation instead of the data — which is exactly
    # what happened, and it failed silently into "no standards retrieved".
    tail = prompt.rsplit("INPUT_JSON:", 1)[1].lstrip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(tail)
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        return {}


def _standards_from_trace(prompt: str) -> list[dict]:
    """Recover the standards the agent retrieved during phase 1.

    Since ADR-009 the decide prompt no longer carries the standards corpus —
    the agent has to go and retrieve it, which is what makes citations
    checkable. The fixture provider therefore has to read them out of the
    investigation trace, exactly as the live model does, rather than from a
    payload key that deliberately no longer exists.
    """
    if "INVESTIGATION_RESULTS:" not in prompt:
        return []
    tail = prompt.rsplit("INVESTIGATION_RESULTS:", 1)[1].lstrip()
    try:
        trace, _ = json.JSONDecoder().raw_decode(tail)
    except ValueError:
        return []
    seen, out = set(), []
    for step in trace if isinstance(trace, list) else []:
        if step.get("tool") != "search_standards":
            continue
        for m in (step.get("result") or {}).get("matches", []):
            if m.get("code") and m["code"] not in seen:
                seen.add(m["code"])
                out.append(m)
    return out


def _estimate_cost(model: str, tin: int, tout: int, input_kind: str = "default") -> float:
    p = _PRICING.get(model, {"input_per_m": 0, "output_per_m": 0})
    input_rate = p.get(f"{input_kind}_input_per_m", p["input_per_m"])
    return round(tin / 1e6 * input_rate + tout / 1e6 * p["output_per_m"], 6)


def _log_call(*, tenant_id: str, audit_id: str | None, purpose: str, provider: str,
              model: str, tin: int, tout: int, latency_ms: int, ok: bool,
              retries: int = 0, cache_hit: bool = False,
              input_kind: str = "default") -> None:
    db = SessionLocal()
    db.add(ModelCall(id=uid("call"), tenant_id=tenant_id, audit_id=audit_id,
                     purpose=purpose, provider=provider, model=model,
                     input_tokens=tin, output_tokens=tout, latency_ms=latency_ms,
                     est_cost_usd=_estimate_cost(model, tin, tout, input_kind),
                     schema_retries=retries, ok=ok, cache_hit=cache_hit))
    db.commit()
    db.close()


# --------------------------------------------------------------------------
# Gemini (live)
# --------------------------------------------------------------------------

def _gemini_schema(node):
    """Normalise JSON-Schema dicts to the casing Gemini's Schema type expects.

    Our tool declarations are written in provider-neutral JSON Schema so the
    same registry can feed a different provider later. Gemini wants its `type`
    values upper-cased; this is the whole of the translation.
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.upper()
            else:
                out[k] = _gemini_schema(v)
        return out
    if isinstance(node, list):
        return [_gemini_schema(v) for v in node]
    return node


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        from google import genai  # lazy import so fixture mode needs no SDK
        if config.GEMINI_VERTEX_PROJECT and config.GEMINI_VERTEX_SA_PATH:
            # Vertex AI route: service-account auth via GOOGLE_APPLICATION_CREDENTIALS
            self._client = genai.Client(vertexai=True,
                                        project=config.GEMINI_VERTEX_PROJECT,
                                        location=config.GEMINI_VERTEX_LOCATION)
        else:
            self._client = genai.Client(api_key=config.GEMINI_API_KEY)

    # -- phase 1: investigate ---------------------------------------------
    def investigate(self, *, purpose: str, prompt: str, tool_declarations: list[dict],
                    execute, tenant_id: str, audit_id: str | None,
                    max_steps: int = 6) -> dict:
        """Bounded read-only tool loop. Returns the trace; decides nothing.

        Automatic function calling is disabled on purpose. The SDK would happily
        run the loop for us, but then the tool calls would not be budgeted,
        logged to the cost ledger, or reconstructable afterwards — and the whole
        claim of this product is that every step is reconstructable.
        """
        from google.genai import types

        decls = [types.FunctionDeclaration(
            name=d["name"], description=d["description"],
            parameters=_gemini_schema(d["parameters"])) for d in tool_declarations]
        cfg = types.GenerateContentConfig(
            system_instruction=_system_prompt(),
            tools=[types.Tool(function_declarations=decls)],
            temperature=0.2,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
        trace: list[dict] = []
        stopped = "model_finished"

        for step in range(max_steps):
            require_model_budget(audit_id)
            start = time.time()
            try:
                resp = self._client.models.generate_content(
                    model=config.LLM_MODEL, contents=contents, config=cfg)
            except Exception:
                _log_call(tenant_id=tenant_id, audit_id=audit_id,
                          purpose=f"{purpose}:investigate", provider=self.name,
                          model=config.LLM_MODEL, tin=0, tout=0,
                          latency_ms=int((time.time() - start) * 1000), ok=False)
                raise
            latency = int((time.time() - start) * 1000)
            usage = getattr(resp, "usage_metadata", None)
            _log_call(tenant_id=tenant_id, audit_id=audit_id,
                      purpose=f"{purpose}:investigate", provider=self.name,
                      model=config.LLM_MODEL,
                      tin=getattr(usage, "prompt_token_count", 0) or 0,
                      tout=getattr(usage, "candidates_token_count", 0) or 0,
                      latency_ms=latency, ok=True)

            calls = list(getattr(resp, "function_calls", None) or [])
            if not calls:
                break
            contents.append(resp.candidates[0].content)
            parts = []
            for fc in calls:
                args = dict(fc.args or {})
                result = execute(fc.name, args)
                trace.append({"step": step + 1, "tool": fc.name, "args": args,
                              "result": result, "latency_ms": latency})
                parts.append(types.Part.from_function_response(
                    name=fc.name, response=result))
            contents.append(types.Content(role="user", parts=parts))
        else:
            stopped = "step_budget_exhausted"

        return {"trace": trace, "steps": len(trace), "stopped": stopped,
                "provider": self.name}

    # -- vision: photo → neutral observation ------------------------------
    def describe_image(self, *, image_bytes: bytes, mime_type: str, zone_hint: str,
                       privacy_level: str, evidence_request: str = "", tenant_id: str,
                       audit_id: str | None) -> "PhotoDescription":
        """Convert a photograph into a described observation. Judges nothing.

        Schema-locked to PhotoDescription, which has no field for a standard, a
        severity or a verdict — the vision model is structurally incapable of
        returning a compliance conclusion, rather than merely instructed not to.
        """
        from google.genai import types
        from .schemas import PhotoDescription

        doc = (config.PROMPTS_DIR / "photo_description.md").read_text()
        context = (f"\n\nZone: {zone_hint or 'not specified'}\n"
                   f"Zone privacy level: {privacy_level or 'NORMAL'}\n"
                   f"Evidence request: {evidence_request or 'standalone field photo'}\n")
        last_error: Exception | None = None
        for attempt in range(2):
            require_model_budget(audit_id)
            start = time.time()
            try:
                resp = self._client.models.generate_content(
                    model=config.LLM_MODEL,
                    contents=[types.Content(role="user", parts=[
                        types.Part(text=doc + context),
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type)])],
                    config=types.GenerateContentConfig(
                        system_instruction=_system_prompt(),
                        response_mime_type="application/json",
                        response_schema=PhotoDescription,
                        temperature=0.1))
                latency = int((time.time() - start) * 1000)
                usage = getattr(resp, "usage_metadata", None)
                parsed = resp.parsed or PhotoDescription.model_validate_json(resp.text)
                _log_call(tenant_id=tenant_id, audit_id=audit_id,
                          purpose="photo_description", provider=self.name,
                          model=config.LLM_MODEL,
                          tin=getattr(usage, "prompt_token_count", 0) or 0,
                          tout=getattr(usage, "candidates_token_count", 0) or 0,
                          latency_ms=latency, ok=True, retries=attempt)
                return parsed
            except Exception as exc:
                last_error = exc
                _log_call(tenant_id=tenant_id, audit_id=audit_id,
                          purpose="photo_description", provider=self.name,
                          model=config.LLM_MODEL, tin=0, tout=0,
                          latency_ms=int((time.time() - start) * 1000),
                          ok=False, retries=attempt)
        raise RuntimeError(f"Gemini photo description failed after retry: {last_error}")

    def describe_media(self, *, media_bytes: bytes, mime_type: str,
                       media_kind: str, zone_hint: str, privacy_level: str,
                       standard_hint: str, tenant_id: str,
                       audit_id: str | None) -> "MediaDescription":
        """Transcribe audio or neutrally describe a short video.

        The schema cannot return a compliance verdict. Audio remains a
        consultant attestation; video contributes only facts visible/audible in
        the supplied clip. The audit agent performs standards retrieval later.
        """
        from google.genai import types
        from .schemas import MediaDescription

        doc = (config.PROMPTS_DIR / "media_description.md").read_text()
        context = (
            f"\n\nMedia kind: {media_kind}\n"
            f"Zone: {zone_hint or 'not specified'}\n"
            f"Zone privacy level: {privacy_level or 'NORMAL'}\n"
            f"Consultant-selected standard context: {standard_hint or 'none'}\n"
        )
        last_error: Exception | None = None
        for attempt in range(2):
            require_model_budget(audit_id)
            start = time.time()
            try:
                resp = self._client.models.generate_content(
                    model=config.LLM_MODEL,
                    contents=[types.Content(role="user", parts=[
                        types.Part(text=doc + context),
                        types.Part.from_bytes(data=media_bytes, mime_type=mime_type),
                    ])],
                    config=types.GenerateContentConfig(
                        system_instruction=_system_prompt(),
                        response_mime_type="application/json",
                        response_schema=MediaDescription,
                        temperature=0.1,
                    ),
                )
                latency = int((time.time() - start) * 1000)
                usage = getattr(resp, "usage_metadata", None)
                parsed = resp.parsed or MediaDescription.model_validate_json(resp.text)
                _log_call(
                    tenant_id=tenant_id, audit_id=audit_id,
                    purpose=f"{media_kind.lower()}_description", provider=self.name,
                    model=config.LLM_MODEL,
                    tin=getattr(usage, "prompt_token_count", 0) or 0,
                    tout=getattr(usage, "candidates_token_count", 0) or 0,
                    latency_ms=latency, ok=True, retries=attempt,
                    input_kind="audio" if media_kind == "AUDIO" else "default",
                )
                return parsed
            except Exception as exc:
                last_error = exc
                _log_call(
                    tenant_id=tenant_id, audit_id=audit_id,
                    purpose=f"{media_kind.lower()}_description", provider=self.name,
                    model=config.LLM_MODEL, tin=0, tout=0,
                    latency_ms=int((time.time() - start) * 1000),
                    ok=False, retries=attempt,
                )
        raise RuntimeError(
            f"Gemini {media_kind.lower()} description failed after retry: {last_error}")

    # -- phase 2: decide ---------------------------------------------------
    def generate(self, *, purpose: str, prompt: str, schema: Type[T],
                 tenant_id: str, audit_id: str | None) -> T:
        last_err: Exception | None = None
        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        base_prompt = f"{prompt}\n\nOUTPUT_JSON_SCHEMA:{schema_json}"
        attempt_prompt = base_prompt
        gen_config: dict = {
            "system_instruction": _system_prompt(),
            "response_mime_type": "application/json",
            "temperature": 0.2,
        }
        if config.LLM_THINKING_BUDGET is not None:
            gen_config["thinking_config"] = {"thinking_budget": config.LLM_THINKING_BUDGET}
        for attempt in range(2):  # one retry on schema violation
            require_model_budget(audit_id)
            start = time.time()
            try:
                resp = self._client.models.generate_content(
                    model=config.LLM_MODEL,
                    contents=attempt_prompt,
                    config=gen_config,
                )
            except Exception as e:
                last_err = e
                latency = int((time.time() - start) * 1000)
                _log_call(tenant_id=tenant_id, audit_id=audit_id, purpose=purpose,
                          provider=self.name, model=config.LLM_MODEL, tin=0,
                          tout=0, latency_ms=latency, ok=False, retries=attempt)
                attempt_prompt = (f"{base_prompt}\n\nThe previous model call failed: "
                                  f"{str(e)[:800]}. Return ONLY JSON that matches "
                                  "OUTPUT_JSON_SCHEMA exactly.")
                continue
            latency = int((time.time() - start) * 1000)
            usage = getattr(resp, "usage_metadata", None)
            tin = getattr(usage, "prompt_token_count", 0) or 0
            tout = getattr(usage, "candidates_token_count", 0) or 0
            try:
                parsed = resp.parsed
                if parsed is None:
                    parsed = schema.model_validate_json(resp.text)
                _log_call(tenant_id=tenant_id, audit_id=audit_id, purpose=purpose,
                          provider=self.name, model=config.LLM_MODEL, tin=tin,
                          tout=tout, latency_ms=latency, ok=True, retries=attempt)
                return parsed  # type: ignore[return-value]
            except Exception as e:  # schema violation → retry once with the error
                last_err = e
                _log_call(tenant_id=tenant_id, audit_id=audit_id, purpose=purpose,
                          provider=self.name, model=config.LLM_MODEL, tin=tin,
                          tout=tout, latency_ms=latency, ok=False, retries=attempt)
                attempt_prompt = (f"{base_prompt}\n\nYour previous response failed schema "
                                  f"validation: {str(e)[:800]}. Return ONLY JSON that "
                                  "matches OUTPUT_JSON_SCHEMA exactly.")
        raise RuntimeError(f"Gemini structured output failed after retry: {last_err}")


# --------------------------------------------------------------------------
# Fixture (deterministic policy engine — keyless demo + eval baseline)
# --------------------------------------------------------------------------

_VAGUE = re.compile(r"\b(a little|kinda|kind of|somewhat|looked (dirty|off|bad|slow)|"
                    r"seemed|maybe|not great|slightly)\b", re.I)
_SPECIFIC = re.compile(r"\b(overflow\w*|standing water|spill\w*|debris|broken|crack\w*|missing|"
                       r"blocked|expired|leak\w*|odou?rs?|out of order|no (soap|paper)|propped open|"
                       r"unsecured|unlabell?ed|unlabeled|on the floor|across the (walkway|lane)|"
                       r"flicker\w*|fault\w*|trip hazard|no wet.floor sign|slip hazard|"
                       r"unavailable|absent|not present|not on duty|no (guard|officer)|"
                       r"\d+ ?(min|minutes|hours))\b", re.I)
_POSITIVE = re.compile(r"\b(clean|excellent|good condition|no issues?|well maintained|"
                       r"fine|great|passed)\b", re.I)
_NEGATED_POSITIVE = re.compile(
    r"\b(?:not|never|wasn['â€™]?t|isn['â€™]?t|aren['â€™]?t|no longer)\s+"
    r"(?:very\s+)?(?:clean|excellent|in good condition|well maintained|fine|great|passed)\b",
    re.I,
)
_INJECTION = re.compile(r"(ignore (all )?(previous|prior) instructions|reveal|api key|"
                        r"system prompt)", re.I)

_KEYWORD_STANDARD = [
    (re.compile(r"security|guard|security officer|entrance officer|cctv", re.I), "security_presence"),
    (re.compile(r"restroom|bathroom|toilet", re.I), "cleanliness"),
    (re.compile(r"floor|spill|litter", re.I), "cleanliness"),
    (re.compile(r"cart path|walkway|trip", re.I), "safety"),
    (re.compile(r"chemical|storage room|unsecured|unlabel", re.I), "safety"),
    (re.compile(r"sign|signage", re.I), "signage"),
    (re.compile(r"check-?in|wait|queue|register", re.I), "operations"),
    (re.compile(r"pace|interval|tee sheet", re.I), "operations"),
    (re.compile(r"food|kitchen|temperature|sanitis|sanitiz", re.I), "food_safety"),
    (re.compile(r"green|fairway|turf|bunker", re.I), "course_condition"),
    (re.compile(r"cable|charger|charging|bay", re.I), "safety"),
    (re.compile(r"battery", re.I), "safety"),
    (re.compile(r"dispatch|sla", re.I), "operations"),
]


class FixtureProvider:
    """Deterministic stand-in that follows the same policy the prompts encode.

    It is intentionally conservative: when in doubt it clarifies. Responses are
    labelled provider='fixture' in the cost ledger.
    """
    name = "fixture"

    def describe_media(self, **kwargs):
        raise RuntimeError(
            "Audio/video analysis requires a live multimodal model. There is no "
            "fixture transcript or description because fabricated media evidence "
            "would be indistinguishable from a real capture.")

    def investigate(self, *, purpose: str, prompt: str, tool_declarations: list[dict],
                    execute, tenant_id: str, audit_id: str | None,
                    max_steps: int = 6) -> dict:
        """Deterministic stand-in for the tool loop.

        It calls the same tools through the same dispatcher the live model uses,
        so the keyless demo produces a real trace over real data — the tool
        layer is exercised even when no model is present. Only the *choice* of
        which tools to call is scripted.
        """
        require_model_budget(audit_id)
        start = time.time()
        payload = _extract_payload(prompt)
        trace: list[dict] = []
        step = 0
        for ob in payload.get("observations", []):
            if step >= max_steps:
                break
            text = f"{ob.get('text','')} {ob.get('clarification_answer') or ''}".strip()
            cat = next((c for rx, c in _KEYWORD_STANDARD if rx.search(text)), None)
            step += 1
            args = {"query": text, **({"category": cat} if cat else {})}
            trace.append({"step": step, "tool": "search_standards", "args": args,
                          "result": execute("search_standards", args), "latency_ms": 0})
            if cat and step < max_steps:
                step += 1
                args = {"category": cat}
                trace.append({"step": step, "tool": "location_history", "args": args,
                              "result": execute("location_history", args), "latency_ms": 0})
        _log_call(tenant_id=tenant_id, audit_id=audit_id,
                  purpose=f"{purpose}:investigate", provider=self.name, model="fixture",
                  tin=0, tout=0, latency_ms=int((time.time() - start) * 1000), ok=True)
        return {"trace": trace, "steps": len(trace), "stopped": "model_finished",
                "provider": self.name}

    def describe_image(self, **kwargs):
        """Deliberately unimplemented.

        Every other capability has a deterministic stand-in so the demo runs
        keyless. Vision does not, and must not: a plausible description of a
        photograph nobody looked at would enter the system as evidence and be
        indistinguishable from the real thing. The endpoint returns a clear
        "vision needs a live model" error instead.
        """
        raise RuntimeError(
            "Photo analysis requires a live vision model. There is no fixture "
            "stand-in for vision by design — a fabricated description of an "
            "unseen image would be indistinguishable from evidence.")

    def generate(self, *, purpose: str, prompt: str, schema: Type[T],
                 tenant_id: str, audit_id: str | None) -> T:
        require_model_budget(audit_id)
        start = time.time()
        payload = _extract_payload(prompt)
        if schema is AnalysisResult:
            # Standards come from what phase 1 retrieved, not from the payload.
            payload = {**payload, "standards": _standards_from_trace(prompt)}
            result: BaseModel = self._analyse(payload)
        elif schema is ReviewThemes:
            result = self._themes(payload)
        elif schema is Challenge:
            result = self._challenge(purpose, payload)
        else:
            raise RuntimeError(f"FixtureProvider has no recipe for {schema}")
        _log_call(tenant_id=tenant_id, audit_id=audit_id, purpose=purpose,
                  provider=self.name, model="fixture", tin=0, tout=0,
                  latency_ms=int((time.time() - start) * 1000), ok=True)
        return result  # type: ignore[return-value]

    # -- audit analysis ----------------------------------------------------
    def _analyse(self, payload: dict) -> AnalysisResult:
        standards = payload.get("standards", [])
        by_cat: dict[str, dict] = {}
        for s in standards:
            by_cat.setdefault(s["category"], s)
        decisions: list[ObservationDecision] = []
        for ob in payload.get("observations", []):
            text = ob.get("text", "")
            oid = ob["id"]
            injected = bool(_INJECTION.search(text))
            clean_text = text
            answered = ob.get("clarification_answer") or ""
            merged = f"{clean_text} {answered}".strip()

            if (re.search(r"\bsecurity\b", merged, re.I)
                    and re.search(r"\b(missing|unavailable|absent|not present)\b", merged, re.I)
                    and not re.search(r"\b(guard|officer|personnel|camera|cctv|equipment)\b", merged, re.I)):
                decisions.append(ObservationDecision(
                    observation_id=oid, decision="CLARIFY",
                    clarify=ClarifySpec(
                        question=("When you say security is missing, do you mean the scheduled "
                                  "guard/officer is absent, or that security equipment is missing?"),
                        why_needed=("The people-coverage and equipment conditions are different "
                                    "issues and must not be inferred from the word security."),
                        options=["Scheduled guard/officer absent", "Security equipment missing",
                                 "Both", "Neither / clarify"])))
                continue

            if (_POSITIVE.search(merged) and not _NEGATED_POSITIVE.search(merged)
                    and not _SPECIFIC.search(merged) and not _VAGUE.search(merged)):
                decisions.append(ObservationDecision(
                    observation_id=oid, decision="NO_ISSUE",
                    note=("Observation records acceptable condition." +
                          (" Embedded instruction-like text treated as data, not commands." if injected else ""))))
                continue

            cat = next((c for rx, c in _KEYWORD_STANDARD if rx.search(merged)), None)
            std = by_cat.get(cat) if cat else None

            if _SPECIFIC.search(merged) and std:
                sev = std.get("severity_default", "MEDIUM")
                decisions.append(ObservationDecision(
                    observation_id=oid, decision="CANDIDATE_FINDING",
                    finding=FindingDraft(
                        standard_code=std["code"], category=std["category"],
                        title=f"{std['category'].replace('_', ' ').title()} condition vs {std['code']}",
                        consultant_statement=text,
                        model_interpretation=(f"The consultant's words describe a specific, observable "
                                              f"condition that plausibly falls under {std['code']}. "
                                              "Interpretation is limited to what was stated; no wider "
                                              "condition is assumed."),
                        severity=sev, confidence=0.72,
                        uncertainty_reasons=["Single observation; no corroborating photo evidence attached."],
                        not_supported=["Duration or recurrence of the condition",
                                        "Root cause", "Intent or neglect by staff"],
                        recommended_action=ActionDraft(
                            description=f"Correct the condition described and confirm against {std['code']}.",
                            owner_role="Location Manager", due_in_days=3 if sev in ("HIGH", "CRITICAL") else 7,
                            verification_method="After photo plus manager confirmation")),
                    note="Embedded instruction-like text treated as data." if injected else ""))
            elif _VAGUE.search(merged) or not std:
                area = cat or "the area mentioned"
                decisions.append(ObservationDecision(
                    observation_id=oid, decision="CLARIFY",
                    clarify=ClarifySpec(
                        question=(f"The note about {area} is not specific enough to assess against a "
                                  "standard. What exactly was observed — loose debris, a spill or slip "
                                  "hazard, an odour, or only cosmetic marking? Did it persist after "
                                  "service was called?"),
                        why_needed="A determination requires an observable, specific condition; vague wording cannot support a finding.",
                        options=["Loose debris/litter", "Spill or slip hazard", "Odour/sanitary issue",
                                 "Cosmetic marking only"]),
                    note="Embedded instruction-like text treated as data." if injected else ""))
            else:
                decisions.append(ObservationDecision(
                    observation_id=oid, decision="CLARIFY",
                    clarify=ClarifySpec(
                        question="Which specific condition and location does this note refer to?",
                        why_needed="No applicable standard could be matched to the wording.")))
        return AnalysisResult(
            decisions=decisions,
            overall_summary=(f"{len(decisions)} observation(s) processed: "
                             f"{sum(1 for d in decisions if d.decision=='CANDIDATE_FINDING')} candidate finding(s), "
                             f"{sum(1 for d in decisions if d.decision=='CLARIFY')} clarification(s), "
                             f"{sum(1 for d in decisions if d.decision=='NO_ISSUE')} no-issue."))

    # -- adversarial challenge --------------------------------------------
    def _challenge(self, purpose: str, payload: dict) -> Challenge:
        """Deterministic challenger. Conservative, and honest about being rules.

        It cannot argue, so it checks the two things a rule can check: whether
        the finding claims duration or cause that a single observation cannot
        establish, and whether the model declared any uncertainty at all. A
        finding asserting persistence with an empty uncertainty list is exactly
        the shape that gets successfully disputed.
        """
        interp = (payload.get("model_interpretation") or "").lower()
        overreach = [w for w in ("ongoing", "persistent", "repeatedly", "systemic",
                                 "negligen", "routinely", "always", "root cause")
                     if w in interp]
        if overreach and not payload.get("model_stated_uncertainty"):
            return Challenge(
                verdict="WEAKEN",
                objection_basis="EVIDENCE",
                argument=("The interpretation asserts a continuing or causal state "
                          f"({', '.join(overreach)}) while declaring no uncertainty. A "
                          "single visit observes a moment, not a pattern."),
                specific_gap="Duration, recurrence or cause asserted from one observation.",
                what_would_settle_it=("The inspection log for the period, or a second "
                                      "observation at a different time of day."))
        if not payload.get("model_says_not_supported"):
            return Challenge(
                verdict="WEAKEN",
                objection_basis="EVIDENCE",
                argument=("Nothing is listed as unsupported. Every single observation "
                          "leaves something unestablished; an empty list signals the "
                          "limits were not considered."),
                specific_gap="No stated limits on what the evidence establishes.",
                what_would_settle_it="An explicit statement of what was not observed.")
        return Challenge(
            verdict="UPHOLD",
            objection_basis="NONE",
            argument=("Deterministic checks find no overreach: limits are stated and the "
                      "interpretation does not assert duration or cause."),
            what_would_settle_it="")

    # -- review themes -----------------------------------------------------
    _THEME_RULES = [
        ("Pace of play / slow rounds", re.compile(r"pace|slow|six hours|5\+? ?hours|marshal", re.I),
         [("operations", "Consistent with, but does not prove, the pace-of-play monitoring standard (OPS-02) being under-enforced.")]),
        ("Restroom cleanliness", re.compile(r"restroom|bathroom|toilet", re.I),
         [("cleanliness", "Consistent with, but does not prove, a restroom cleanliness issue under CLN-01.")]),
        ("Check-in wait times", re.compile(r"check-?in|wait|register|queue", re.I),
         [("operations", "Consistent with, but does not prove, check-in staffing below OPS-01 expectations.")]),
        ("Beverage cart availability", re.compile(r"beverage cart|drink cart", re.I),
         [("operations", "Consistent with an F&B route-coverage gap; no field evidence linked.")]),
        ("On-course drinking water availability",
         re.compile(r"no (?:drinking )?water|water (?:filling )?stations?|bring my own.*water", re.I),
         [("operations", "Consistent with, but does not prove, a guest-service and heat-readiness gap; verify water availability on site.")]),
        ("Service responsiveness and value",
         re.compile(r"poor customer.?service|no answer|assistance.*never|not worth|high price|\$\d+|paid over", re.I),
         [("operations", "Consistent with, but does not prove, a service-response or value-expectation gap; compare against staffing and service logs.")]),
        ("Golf cart reliability / GPS controls",
         re.compile(r"cart.*(?:gps|shut|work)|gps.*(?:cart|work)|push our carts", re.I),
         [("operations", "Consistent with, but does not prove, a cart reliability or support-response issue; inspect fleet fault records.")]),
        ("Temporary greens and pre-arrival disclosure",
         re.compile(r"temporary greens?|sanded|informed.*before|before we got there", re.I),
         [("course_condition", "Consistent with, but does not prove, a course-condition communication gap; verify booking and pre-arrival notices.")]),
    ]

    def _themes(self, payload: dict) -> ReviewThemes:
        reviews = payload.get("reviews", [])
        recent_negative = [r for r in reviews
                           if (r.get("rating") or 5) <= 3
                           and r.get("days_ago") is not None
                           and 0 <= r["days_ago"] <= 92]
        themes, anecdotes = [], []
        for name, rx, links in self._THEME_RULES:
            hits = [r for r in recent_negative if rx.search(r.get("text", ""))]
            if len(hits) >= 2:
                themes.append(ReviewTheme(
                    theme=name, mention_count=len(hits),
                    review_ids=[h.get("id", "") for h in hits],
                    linked_categories=[ThemeCategoryLink(category=c, language=lang) for c, lang in links]))
            elif len(hits) == 1:
                anecdotes.append(f"{name} (single mention — not a recurring theme)")
        return ReviewThemes(negative_recent_count=len(recent_negative), themes=themes, anecdotes=anecdotes)


# --------------------------------------------------------------------------

_provider = None
_fallback_reason: str | None = None
_fallback_by_audit: dict[str, str] = {}
_last_live_success_at: str | None = None


class ResilientProvider:
    """Wraps the live provider with automatic, LABELLED fixture fallback.

    Spec §19.5: Gemini unavailable → deterministic flows still work. A demo
    must degrade, never 500. The fallback is recorded (cost ledger + status)
    so nobody mistakes fixture output for live model output."""
    def __init__(self, live) -> None:
        self._live = live
        self._fixture = FixtureProvider()

    @property
    def name(self) -> str:
        return self._live.name

    def generate(self, **kwargs):
        global _fallback_reason, _last_live_success_at
        try:
            result = self._live.generate(**kwargs)
            _fallback_reason = None
            _last_live_success_at = datetime.now(timezone.utc).isoformat()
            return result
        except ModelBudgetExceeded:
            raise
        except Exception as e:  # transport/auth/provider failure → labelled fallback
            _fallback_reason = f"{type(e).__name__}: {str(e)[:120]}"
            if kwargs.get("audit_id"):
                _fallback_by_audit[str(kwargs["audit_id"])] = _fallback_reason
            return self._fixture.generate(**kwargs)

    def investigate(self, **kwargs):
        global _fallback_reason, _last_live_success_at
        try:
            result = self._live.investigate(**kwargs)
            _fallback_reason = None
            _last_live_success_at = datetime.now(timezone.utc).isoformat()
            return result
        except ModelBudgetExceeded:
            raise
        except Exception as e:
            _fallback_reason = f"{type(e).__name__}: {str(e)[:120]}"
            if kwargs.get("audit_id"):
                _fallback_by_audit[str(kwargs["audit_id"])] = _fallback_reason
            out = self._fixture.investigate(**kwargs)
            out["degraded"] = True
            out["degraded_reason"] = _fallback_reason
            return out

    def describe_image(self, **kwargs):
        # No fixture fallback: a fabricated description of a photo nobody looked
        # at is worse than an error. The caller surfaces the failure instead.
        global _fallback_reason, _last_live_success_at
        result = self._live.describe_image(**kwargs)
        _fallback_reason = None
        _last_live_success_at = datetime.now(timezone.utc).isoformat()
        return result

    def describe_media(self, **kwargs):
        # Media evidence has the same no-fabricated-fallback rule as photos.
        global _fallback_reason, _last_live_success_at
        result = self._live.describe_media(**kwargs)
        _fallback_reason = None
        _last_live_success_at = datetime.now(timezone.utc).isoformat()
        return result


class SynchronizedProvider:
    """Make budget preflight + provider invocation atomic in one worker."""
    def __init__(self, provider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        return self._provider.name

    def generate(self, **kwargs):
        with model_workflow_lock():
            return self._provider.generate(**kwargs)

    def investigate(self, **kwargs):
        with model_workflow_lock():
            return self._provider.investigate(**kwargs)

    def describe_image(self, **kwargs):
        with model_workflow_lock():
            return self._provider.describe_image(**kwargs)

    def describe_media(self, **kwargs):
        with model_workflow_lock():
            return self._provider.describe_media(**kwargs)


def get_provider():
    global _provider
    if _provider is None:
        if config.LLM_PROVIDER == "gemini" and config.GEMINI_CONFIGURED:
            _provider = SynchronizedProvider(ResilientProvider(GeminiProvider()))
        else:
            _provider = SynchronizedProvider(FixtureProvider())
    return _provider


def provider_status() -> dict:
    p = get_provider()
    if p.name == "gemini":
        route = "Vertex AI (service account)" if config.GEMINI_VERTEX_PROJECT else "Gemini API key"
        status = {"active_provider": p.name, "configured_provider": "gemini",
                  "reason": (f"live call confirmed via {route}" if _last_live_success_at
                             else f"configured via {route}; no successful call confirmed yet"),
                  "readiness": ("LIVE_CALL_CONFIRMED" if _last_live_success_at
                                else "CONFIGURED_NOT_PROBED"),
                  "last_live_success_at": _last_live_success_at}
        if _fallback_reason:
            status["active_provider"] = "fixture"
            status["degraded"] = True
            status["reason"] = f"configured via {route} but last call fell back to fixture ({_fallback_reason})"
        return status
    explicit = config.LLM_PROVIDER != "gemini"
    return {"active_provider": p.name, "configured_provider": config.LLM_PROVIDER,
            "readiness": "FIXTURE_ACTIVE", "last_live_success_at": None,
            "reason": ("Deterministic fixture engine explicitly selected (labelled)" if explicit
                       else "Gemini not configured — deterministic fixture engine active (labelled)")}


def clear_provider_execution(audit_id: str) -> None:
    _fallback_by_audit.pop(audit_id, None)


def provider_execution(audit_id: str) -> dict:
    reason = _fallback_by_audit.get(audit_id)
    if reason:
        return {"provider": "fixture", "degraded": True, "reason": reason}
    return {"provider": get_provider().name, "degraded": False, "reason": None}
