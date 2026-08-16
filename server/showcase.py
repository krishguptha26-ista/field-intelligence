"""Deterministic, honest showcase audit for a fast assessment walkthrough.

The showcase uses the real domain tables and the same API/UI paths as a live
visit.  Its small SVG evidence panels are explicitly labelled DEMO EVIDENCE so
they demonstrate lineage and workflow without pretending to be Wolf Creek
photographs.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from . import config
from .field_guide import ZONE_CHECK_CODES, issue_photo_policy
from .models import (Action, AuditLog, AuditSession, ClarificationQuestion,
                     EvidenceItem, Finding, ModelCall, Observation,
                     OperationalTicket, SessionLocal, Standard, Zone)
from .regulatory import standard_metadata


SHOWCASE_AUDIT_ID = "audit_showcase_wolf_creek"
SHOWCASE_SUPPORT_AUDIT_ID = "audit_showcase_support_restroom"


def _demo_svg(name: str, title: str, subtitle: str, accent: str) -> str:
    body = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760">
<rect width="1200" height="760" fill="#101b2d"/><rect x="45" y="45" width="1110" height="670" rx="28" fill="#f7f4ec"/>
<rect x="45" y="45" width="1110" height="125" rx="28" fill="{accent}"/>
<text x="90" y="105" font-family="Arial,sans-serif" font-size="28" font-weight="700" fill="#111827">DEMO EVIDENCE · {name}</text>
<text x="90" y="245" font-family="Arial,sans-serif" font-size="52" font-weight="700" fill="#101828">{title}</text>
<text x="90" y="310" font-family="Arial,sans-serif" font-size="28" fill="#475467">{subtitle}</text>
<rect x="90" y="380" width="1020" height="220" rx="22" fill="#e8edf5" stroke="#9aa8bc" stroke-width="4"/>
<circle cx="220" cy="490" r="62" fill="{accent}"/><path d="M360 440h610M360 490h520M360 540h420" stroke="#667085" stroke-width="24" stroke-linecap="round"/>
<text x="90" y="660" font-family="Arial,sans-serif" font-size="22" fill="#667085">Illustrative fixture · not a photograph of Wolf Creek Golf Club</text>
</svg>'''
    raw = body.encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = config.UPLOADS_DIR / f"{digest}.svg"
    if not path.exists():
        path.write_bytes(raw)
    return digest


def _challenge(outcome: str, verdicts: tuple[str, str, str]) -> dict:
    lenses = ("evidence_sufficiency", "franchisee_advocate", "standards_fit")
    arguments = {
        "evidence_sufficiency": "Checked whether the consultant statement and linked evidence support the proposed condition.",
        "franchisee_advocate": "Looked for a benign explanation, scope error or evidence that would make the proposal unfair.",
        "standards_fit": "Checked that the cited requirement applies to this location, zone and observed condition.",
    }
    challenges = [{
        "lens": lens, "verdict": verdict, "argument": arguments[lens],
        "specific_gap": "" if verdict == "UPHOLD" else "The available view does not establish the full required scope.",
        "what_would_settle_it": "A wider targeted photo or current controlled record." if verdict != "UPHOLD" else "",
    } for lens, verdict in zip(lenses, verdicts)]
    return {
        "ran": True, "outcome": outcome,
        "votes": {key: verdicts.count(key.upper()) for key in ("uphold", "weaken", "overturn")},
        "challenges": challenges,
        "provenance": "DEMO_FIXTURE",
    }


def _snapshot(standard: Standard) -> dict:
    return {
        "code": standard.code, "text": standard.text,
        "category": standard.category, "source_label": standard.source_label,
        "authoritative": False, **standard_metadata(standard.code),
    }


def _trace(standard: Standard, *, history: str = "No matching prior issue.") -> list[dict]:
    return [
        {"tool": "search_standards", "args": {"query": standard.code},
         "result": {"retrieved_codes": [standard.code], "provenance": standard.source_label}},
        {"tool": "location_history", "args": {"category": standard.category},
         "result": {"summary": history}},
        {"tool": "standard_snapshot", "args": {"standard_id": standard.id},
         "result": _snapshot(standard)},
    ]


def _checklist_rows(db, *, security_photo_id: str, restroom_photo_id: str,
                    security_finding_id: str, restroom_finding_id: str) -> list[dict]:
    detail_by_code = {
        "SIG-01": "Signs in this zone were viewed at walking and vehicle approach distance; wording was legible and panels were undamaged.",
        "SEC-01": "Scheduled entrance guard was absent from 08:05–08:22; manager confirmed the uncovered post.",
        "OSHA-WALK-01": "Employee walking and access surfaces in this zone were walked end-to-end; no spill, obstruction or damaged surface was present.",
        "ADA-PARK-01": "Accessible spaces, access aisle, route and entrance connection were walked; markings and clear widths were unobstructed.",
        "GA-FIRE-01": "Exit routes and posted occupant-safety information in this zone were visually checked and unobstructed.",
        "OPS-01": "Check-in queue observed from 08:35–08:50; longest measured wait was 3 minutes 40 seconds.",
        "CLN-01": "At 10:12 the second sink had standing water and the inspection sheet had no entry after 08:00.",
        "ADA-GOLF-01": "Accessible route and service feature in this zone were walked and remained available without an obstruction.",
        "GA-FOOD-01": "Fulton County permit displayed and current; inspection report dated 2026-07-18 viewed; handwash sink stocked and cold holding read 38°F at 11:05.",
        "SAF-02": "Cart staging chemicals and maintenance materials were labelled, secured and outside guest reach.",
        "OSHA-HAZCOM-01": "Written hazard communication program, current SDS index and labelled secondary containers were spot-checked with the maintenance lead.",
        "GA-PEST-01": "N/A: no restricted-use pesticide application or storage was in scope during this visit; contractor records were not represented as inspected.",
        "GA-BMP-IPM-01": "IPM scouting log for the current week was viewed; threshold decision and treatment record fields were complete.",
        "WCGC-PACE-01": "First-tee interval and starter sheet were compared with the posted operating policy; exceptions were recorded.",
        "GA-BMP-WATER-01": "Irrigation leak log and wet-area markings were checked; no unmarked active leak was observed on the sampled route.",
        "CRS-01": "Sampled tee, fairway and green condition matched the daily agronomy note; temporary condition communication was present.",
    }
    rows: list[dict] = []
    zones = {zone.name: zone for zone in db.query(Zone).filter_by(
        location_id="wolf-creek-atlanta").all()}
    standards = {row.code: row for row in db.query(Standard).filter_by(
        tenant_id="broadpeak-demo").all()}
    for zone_name, codes in ZONE_CHECK_CODES.items():
        if zone_name not in zones or zone_name.startswith(("Charging", "Battery", "Staging", "Driver", "Customer", "Yard")):
            continue
        for code in codes:
            standard = standards[code]
            response = "PASS"
            evidence_ids: list[str] = []
            finding_id = None
            photo_decision = None
            if code == "SEC-01" and zone_name == "Arrival & entrance signage":
                response, evidence_ids = "FAIL", [security_photo_id]
                finding_id, photo_decision = security_finding_id, "ATTACHED"
            elif code == "CLN-01" and zone_name == "Restrooms":
                response, evidence_ids = "FAIL", [restroom_photo_id]
                finding_id, photo_decision = restroom_finding_id, "ATTACHED"
            elif code == "GA-PEST-01":
                response = "NOT_APPLICABLE"
            rows.append({
                "item": standard.text, "standard_code": code,
                "response": response, "detail": detail_by_code.get(
                    code, f"The {zone_name.lower()} condition described by {code} was specifically checked; no exception was observed."),
                "zone_id": zones[zone_name].id,
                "evidence_observation_ids": evidence_ids,
                "source_label": standard.source_label,
                "standard_metadata": standard_metadata(code),
                "photo_policy": issue_photo_policy(code, category=standard.category,
                                                   severity=standard.severity_default),
                "photo_decision": photo_decision,
                "verification_state": ("PHOTO_ATTACHED_PENDING_REVIEW" if evidence_ids
                                       else "CONSULTANT_REPORTED"),
                **({"finding_id": finding_id, "originating_finding_id": finding_id,
                    "review_required": True, "auto_reconciled": True}
                   if finding_id else {}),
            })
    return rows


def seed_showcase(db) -> None:
    if db.get(AuditSession, SHOWCASE_AUDIT_ID):
        return
    now = datetime.now(timezone.utc)
    visited = now - timedelta(days=2)
    standards = {row.code: row for row in db.query(Standard).filter_by(
        tenant_id="broadpeak-demo").all()}
    required = {"CLN-01", "SEC-01", "ADA-PARK-01"}
    if not required.issubset(standards):
        return

    security_before = _demo_svg("BEFORE", "Entrance post uncovered",
                                "08:05 · scheduled coverage window", "#ef9a75")
    security_after = _demo_svg("AFTER", "Entrance coverage restored",
                               "08:32 · manager-confirmed handoff", "#79c29a")
    restroom_before = _demo_svg("BEFORE", "Standing water at second sink",
                                "10:12 · inspection entry missing", "#ef9a75")
    restroom_after = _demo_svg("AFTER", "Floor dry and inspection logged",
                               "10:46 · awaiting independent verification", "#79c29a")

    support_audit = AuditSession(
        id=SHOWCASE_SUPPORT_AUDIT_ID, tenant_id="broadpeak-demo",
        location_id="wolf-creek-atlanta", consultant_name="Showcase support history (DEMO_FIXTURE)",
        status="SHOWCASE_SUPPORT", created_at=visited - timedelta(days=118),
        updated_at=visited - timedelta(days=116),
    )
    audit = AuditSession(
        id=SHOWCASE_AUDIT_ID, tenant_id="broadpeak-demo",
        location_id="wolf-creek-atlanta",
        consultant_name="Showcase audit · Complete product tour (DEMO_FIXTURE)",
        status="SUBMITTED", created_at=visited, updated_at=now,
    )
    db.add_all([support_audit, audit]); db.flush()

    support_ob = Observation(
        id="ob_showcase_support_restroom", tenant_id="broadpeak-demo",
        audit_id=support_audit.id, zone_id="z1_05", kind="NOTE",
        text="Prior verified restroom correction retained only to demonstrate recurrence.",
        provenance="DEMO_FIXTURE", created_at=visited - timedelta(days=118),
        updated_at=visited - timedelta(days=118))
    voice_ob = Observation(
        id="ob_showcase_voice_security", tenant_id="broadpeak-demo", audit_id=audit.id,
        zone_id="z1_00", kind="AUDIO_TRANSCRIPT",
        text="Security is missing at the entrance.", provenance="DEMO_FIXTURE",
        payload={"transcript_confirmed": True, "duration_seconds": 12,
                 "verification_state": "CONSULTANT_CONFIRMED_TRANSCRIPT"},
        created_at=visited, updated_at=visited)
    security_photo_ob = Observation(
        id="ob_showcase_photo_security", tenant_id="broadpeak-demo", audit_id=audit.id,
        zone_id="z1_00", kind="PHOTO_DESCRIPTION",
        text="Demo evidence panel: scheduled entrance post shown uncovered during the stated window.",
        provenance="DEMO_FIXTURE",
        payload={"image_sha256": security_before, "semantic_match": True,
                 "supports_observation_id": voice_ob.id,
                 "evidence_for_standard_code": "SEC-01",
                 "verification_state": "PHOTO_ATTACHED_PENDING_REVIEW"},
        created_at=visited + timedelta(minutes=8), updated_at=visited + timedelta(minutes=8))
    restroom_ob = Observation(
        id="ob_showcase_restroom", tenant_id="broadpeak-demo", audit_id=audit.id,
        zone_id="z1_05", kind="NOTE",
        text="Second sink had standing water at 10:12 and the inspection sheet had no entry after 08:00.",
        provenance="DEMO_FIXTURE", created_at=visited + timedelta(hours=2),
        updated_at=visited + timedelta(hours=2))
    restroom_photo_ob = Observation(
        id="ob_showcase_photo_restroom", tenant_id="broadpeak-demo", audit_id=audit.id,
        zone_id="z1_05", kind="PHOTO_DESCRIPTION",
        text="Demo evidence panel: standing water at the second sink and missed inspection entry.",
        provenance="DEMO_FIXTURE",
        payload={"image_sha256": restroom_before, "semantic_match": True,
                 "supports_observation_id": restroom_ob.id,
                 "evidence_for_standard_code": "CLN-01",
                 "verification_state": "PHOTO_ATTACHED_PENDING_REVIEW"},
        created_at=visited + timedelta(hours=2, minutes=2),
        updated_at=visited + timedelta(hours=2, minutes=2))
    access_ob = Observation(
        id="ob_showcase_accessibility", tenant_id="broadpeak-demo", audit_id=audit.id,
        zone_id="z1_01", kind="WRITTEN_PHOTO_DESCRIPTION",
        text="One close-cropped view appeared to show a narrow accessible aisle, but no measurement or full route was captured.",
        provenance="DEMO_FIXTURE", created_at=visited + timedelta(minutes=35),
        updated_at=visited + timedelta(minutes=35))
    db.add_all([support_ob, voice_ob, security_photo_ob, restroom_ob,
                restroom_photo_ob, access_ob]); db.flush()

    evidence = [
        EvidenceItem(id="ev_showcase_voice", tenant_id="broadpeak-demo", location_id="wolf-creek-atlanta",
                     source_type="AUDIO_TRANSCRIPT", collection_method="UPLOAD", provenance="DEMO_FIXTURE",
                     trust_class="CONSULTANT_OBSERVATION", excerpt="Confirmed transcript: entrance security guard absent, not missing equipment.",
                     observed_at=visited, payload={"audit_id": audit.id, "observation_id": voice_ob.id,
                                                   "duration_seconds": 12, "transcript_confirmed": True}),
        EvidenceItem(id="ev_showcase_security_before", tenant_id="broadpeak-demo", location_id="wolf-creek-atlanta",
                     source_type="PHOTO", collection_method="CAMERA", provenance="DEMO_FIXTURE",
                     trust_class="CONSULTANT_OBSERVATION", excerpt="Targeted demo before evidence for the uncovered entrance post.",
                     observed_at=visited, payload={"audit_id": audit.id, "observation_id": security_photo_ob.id,
                                                  "image_sha256": security_before, "semantic_match": True}),
        EvidenceItem(id="ev_showcase_restroom_before", tenant_id="broadpeak-demo", location_id="wolf-creek-atlanta",
                     source_type="PHOTO", collection_method="CAMERA", provenance="DEMO_FIXTURE",
                     trust_class="CONSULTANT_OBSERVATION", excerpt="Targeted demo before evidence for standing water at the sink.",
                     observed_at=visited + timedelta(hours=2), payload={"audit_id": audit.id,
                         "observation_id": restroom_photo_ob.id, "image_sha256": restroom_before,
                         "semantic_match": True}),
        EvidenceItem(id="ev_showcase_accessibility_text", tenant_id="broadpeak-demo", location_id="wolf-creek-atlanta",
                     source_type="WRITTEN_PHOTO_DESCRIPTION", collection_method="MANUAL", provenance="DEMO_FIXTURE",
                     trust_class="CONSULTANT_OBSERVATION", excerpt=access_ob.text,
                     observed_at=visited + timedelta(minutes=35), payload={"audit_id": audit.id,
                         "observation_id": access_ob.id, "photo_available": False}),
    ]
    db.add_all(evidence); db.flush()

    support_finding = Finding(
        id="finding_showcase_support_restroom", tenant_id="broadpeak-demo",
        audit_id=support_audit.id, observation_id=support_ob.id, category="cleanliness",
        title="Prior restroom service lapse", status="APPROVED",
        standard_id=standards["CLN-01"].id, evidence_ids=[],
        consultant_statement=support_ob.text,
        model_interpretation="Verified prior correction retained as hidden showcase support history.",
        severity="HIGH", confidence=.8, uncertainty_reasons=[],
        not_supported=["Conditions between visits"],
        recommended_action={"description": "Restore inspection round", "owner_role": "Facilities Lead"},
        review_history=[{"at": (visited - timedelta(days=118)).isoformat(), "actor": "Reviewer (DEMO)",
                         "action": "approve", "reason": "Prior on-site validation"}],
        reasoning_trace=_trace(standards["CLN-01"]),
        created_at=visited - timedelta(days=118), updated_at=visited - timedelta(days=116))
    security_finding = Finding(
        id="finding_showcase_security", tenant_id="broadpeak-demo", audit_id=audit.id,
        observation_id=voice_ob.id, category="security", title="Scheduled entrance security post uncovered",
        status="APPROVED", standard_id=standards["SEC-01"].id,
        evidence_ids=["ev_showcase_voice", "ev_showcase_security_before"],
        consultant_statement="Entrance guard absent from 08:05–08:22 during the scheduled coverage window; equipment remained present.",
        model_interpretation="The confirmed voice clarification and targeted photo support an operational security-coverage exception for human review.",
        severity="HIGH", confidence=.91,
        uncertainty_reasons=["The model did not independently access the staffing roster."],
        not_supported=["Why the guard was absent", "Any criminal or legal conclusion"],
        recommended_action={"description": "Restore entrance coverage and add a supervisor handoff check.",
                            "owner_role": "Jordan Lee · Location Operations (DEMO)",
                            "suggested_due_date": (visited + timedelta(days=1)).date().isoformat(),
                            "verification_method": "Before/after evidence plus independent manager verification"},
        review_history=[{"at": (visited + timedelta(hours=5)).isoformat(), "actor": "Reviewer (DEMO)",
                         "action": "approve", "reason": "Clarification and targeted evidence support the exception."}],
        reasoning_trace=_trace(standards["SEC-01"]), challenge_record=_challenge("UPHELD", ("UPHOLD", "UPHOLD", "UPHOLD")),
        created_at=visited + timedelta(minutes=10), updated_at=visited + timedelta(hours=5))
    restroom_finding = Finding(
        id="finding_showcase_restroom", tenant_id="broadpeak-demo", audit_id=audit.id,
        observation_id=restroom_ob.id, category="cleanliness", title="Restroom inspection round missed with standing water",
        status="APPROVED", standard_id=standards["CLN-01"].id,
        evidence_ids=["ev_showcase_restroom_before"], consultant_statement=restroom_ob.text,
        model_interpretation="The observed condition and missed log entry are consistent with CLN-01 and match a verified prior event.",
        severity="HIGH", confidence=.88,
        uncertainty_reasons=["The exact duration of the standing water is unknown."],
        not_supported=["Root cause", "Conditions outside the observed window"],
        recommended_action={"description": "Dry the floor, complete the missed inspection and restore the two-hour round.",
                            "owner_role": "Alex Morgan · Facilities Lead (DEMO)",
                            "suggested_due_date": visited.date().isoformat(),
                            "verification_method": "New after photo and independent verification"},
        review_history=[{"at": (visited + timedelta(hours=5, minutes=10)).isoformat(), "actor": "Reviewer (DEMO)",
                         "action": "approve", "reason": "Condition is photo-backed and recurrence is material."}],
        reasoning_trace=_trace(standards["CLN-01"], history="Verified closed finding 118 days earlier."),
        recurrence={"matched": True, "closed_and_verified": True,
                    "prior_finding_id": support_finding.id,
                    "corrective_action": "Restore inspection round",
                    "days_since_prior": 118,
                    "summary": "Recurrence: a restroom cleanliness finding was verified closed 118 days earlier and has returned."},
        challenge_record=_challenge("UPHELD", ("UPHOLD", "UPHOLD", "UPHOLD")),
        created_at=visited + timedelta(hours=2, minutes=5), updated_at=visited + timedelta(hours=5, minutes=10))
    access_finding = Finding(
        id="finding_showcase_accessibility", tenant_id="broadpeak-demo", audit_id=audit.id,
        observation_id=access_ob.id, category="accessibility",
        title="Accessible parking aisle may be narrow", status="REJECTED",
        standard_id=standards["ADA-PARK-01"].id, evidence_ids=["ev_showcase_accessibility_text"],
        consultant_statement=access_ob.text,
        model_interpretation="The close-cropped description suggested a possible dimensional issue but did not establish one.",
        severity="HIGH", confidence=.46,
        uncertainty_reasons=["No measurement", "Full accessible route not shown"],
        not_supported=["A dimensional nonconformance", "Whether the route was obstructed"],
        recommended_action={"description": "Capture a measured full-route view before making a finding.",
                            "owner_role": "Field Consultant"},
        review_history=[{"at": (visited + timedelta(hours=5, minutes=20)).isoformat(), "actor": "Reviewer (DEMO)",
                         "action": "reject", "reason": "Insufficient evidence; do not convert a possibility into a finding."}],
        reasoning_trace=_trace(standards["ADA-PARK-01"]),
        challenge_record=_challenge("OVERTURNED", ("OVERTURN", "OVERTURN", "WEAKEN")),
        created_at=visited + timedelta(minutes=40), updated_at=visited + timedelta(hours=5, minutes=20))
    db.add_all([support_finding, security_finding, restroom_finding, access_finding]); db.flush()

    support_action = Action(
        id="act_showcase_support_restroom", tenant_id="broadpeak-demo", finding_id=support_finding.id,
        description="Restore inspection round", owner_role="Facilities Lead", status="VERIFIED",
        verification_method="Manager verification", events=[{"at": (visited - timedelta(days=116)).isoformat(),
        "event": "VERIFIED", "by": "Brand Leader (DEMO)"}], created_at=visited - timedelta(days=118),
        updated_at=visited - timedelta(days=116))
    security_action = Action(
        id="act_showcase_security", tenant_id="broadpeak-demo", finding_id=security_finding.id,
        description=security_finding.recommended_action["description"],
        owner_role=security_finding.recommended_action["owner_role"],
        due_date=security_finding.recommended_action["suggested_due_date"],
        verification_method=security_finding.recommended_action["verification_method"], status="VERIFIED",
        events=[{"at": (visited + timedelta(hours=5)).isoformat(), "event": "CREATED", "by": "Reviewer (DEMO)"},
                {"at": (visited + timedelta(hours=7)).isoformat(), "event": "ACTION_RESOLUTION_SUBMITTED", "by": "Location Operator (DEMO)"},
                {"at": (visited + timedelta(hours=9)).isoformat(), "event": "VERIFIED", "by": "Brand Leader (DEMO)"}],
        created_at=visited + timedelta(hours=5), updated_at=visited + timedelta(hours=9))
    restroom_action = Action(
        id="act_showcase_restroom", tenant_id="broadpeak-demo", finding_id=restroom_finding.id,
        description=restroom_finding.recommended_action["description"],
        owner_role=restroom_finding.recommended_action["owner_role"],
        due_date=restroom_finding.recommended_action["suggested_due_date"],
        verification_method=restroom_finding.recommended_action["verification_method"], status="COMPLETE_UNVERIFIED",
        events=[{"at": (visited + timedelta(hours=5, minutes=10)).isoformat(), "event": "CREATED", "by": "Reviewer (DEMO)"},
                {"at": (visited + timedelta(hours=7, minutes=15)).isoformat(), "event": "ACTION_RESOLUTION_SUBMITTED", "by": "Location Operator (DEMO)"}],
        created_at=visited + timedelta(hours=5, minutes=10), updated_at=visited + timedelta(hours=7, minutes=15))
    db.add_all([support_action, security_action, restroom_action]); db.flush()

    security_ticket = OperationalTicket(
        id="ticket_showcase_security", tenant_id="broadpeak-demo", location_id="wolf-creek-atlanta",
        dedupe_key="showcase|security|entrance", source_kind="PHOTO_BACKED_FIELD_FINDING",
        source_refs=[security_finding.id, security_action.id, voice_ob.id, security_photo_ob.id],
        category="security", title="Restore scheduled entrance coverage", description=security_finding.model_interpretation,
        priority="HIGH", assigned_role="Jordan Lee · Location Operations (DEMO)", status="CLOSED_VERIFIED",
        validity_status="VALIDATED_BY_FINDING_REVIEW", due_date=(visited + timedelta(days=1)).date().isoformat(),
        before_evidence=[{"digest": security_before, "note": "Scheduled entrance post uncovered (DEMO EVIDENCE)",
                          "actor": "Field Consultant (DEMO)", "at": visited.isoformat()}],
        after_evidence=[{"digest": security_after, "note": "Coverage restored and handoff recorded (DEMO EVIDENCE)",
                         "actor": "Location Operator (DEMO)", "at": (visited + timedelta(hours=7)).isoformat()}],
        external_reply={"status": "DRAFT_ONLY", "comment": "Thank you for flagging the arrival coverage gap. The scheduled post has been restored and the supervisor handoff check updated.",
                        "note": "Not published; Business Profile authorization is required."},
        events=[{"at": visited.isoformat(), "event": "AUTO_RAISED_FROM_FIELD_EVIDENCE", "by": "SYSTEM"},
                {"at": (visited + timedelta(hours=5)).isoformat(), "event": "FINDING_REVIEW_VALIDATED", "by": "Reviewer (DEMO)"},
                {"at": (visited + timedelta(hours=7)).isoformat(), "event": "RESOLUTION_SUBMITTED", "by": "Location Operator (DEMO)"},
                {"at": (visited + timedelta(hours=9)).isoformat(), "event": "INDEPENDENTLY_VERIFIED", "by": "Brand Leader (DEMO)"}],
        resolved_at=visited + timedelta(hours=9), created_at=visited, updated_at=visited + timedelta(hours=9))
    restroom_ticket = OperationalTicket(
        id="ticket_showcase_restroom", tenant_id="broadpeak-demo", location_id="wolf-creek-atlanta",
        dedupe_key="showcase|restroom|recurrence", source_kind="PHOTO_BACKED_FIELD_FINDING",
        source_refs=[restroom_finding.id, restroom_action.id, restroom_ob.id, restroom_photo_ob.id],
        category="cleanliness", title="Recurring restroom inspection lapse", description=restroom_finding.model_interpretation,
        priority="HIGH", assigned_role="Alex Morgan · Facilities Lead (DEMO)", status="RESOLVED_PENDING_VERIFICATION",
        validity_status="VALIDATED_BY_FINDING_REVIEW", due_date=visited.date().isoformat(),
        before_evidence=[{"digest": restroom_before, "note": "Standing water and missed log entry (DEMO EVIDENCE)",
                          "actor": "Field Consultant (DEMO)", "at": (visited + timedelta(hours=2)).isoformat()}],
        after_evidence=[{"digest": restroom_after, "note": "Floor dry and inspection entry restored (DEMO EVIDENCE)",
                         "actor": "Location Operator (DEMO)", "at": (visited + timedelta(hours=7, minutes=15)).isoformat()}],
        events=[{"at": (visited + timedelta(hours=2)).isoformat(), "event": "AUTO_RAISED_FROM_FIELD_EVIDENCE", "by": "SYSTEM"},
                {"at": (visited + timedelta(hours=5, minutes=10)).isoformat(), "event": "FINDING_REVIEW_VALIDATED", "by": "Reviewer (DEMO)"},
                {"at": (visited + timedelta(hours=7, minutes=15)).isoformat(), "event": "RESOLUTION_SUBMITTED", "by": "Location Operator (DEMO)"}],
        created_at=visited + timedelta(hours=2), updated_at=visited + timedelta(hours=7, minutes=15))
    db.add_all([security_ticket, restroom_ticket])

    db.add_all([
        ClarificationQuestion(id="q_showcase_security_scope", tenant_id="broadpeak-demo", audit_id=audit.id,
            observation_id=voice_ob.id, question="Is the missing security element the assigned guard, equipment, or both?",
            why_needed="Distinguish staffing coverage from equipment availability.", options=["Guard", "Equipment", "Both"],
            answer="The scheduled guard was absent; equipment was present.", status="ANSWERED", created_at=visited, updated_at=visited),
        ClarificationQuestion(id="q_showcase_security_window", tenant_id="broadpeak-demo", audit_id=audit.id,
            observation_id=voice_ob.id, question="Was entrance coverage required during the observed period?",
            why_needed="Confirm the operating-plan condition before proposing an exception.", options=["Yes", "No", "Unknown"],
            answer="Yes — the manager confirmed coverage was scheduled from opening.", status="ANSWERED", created_at=visited + timedelta(minutes=3), updated_at=visited + timedelta(minutes=3)),
        ClarificationQuestion(id="q_showcase_security_photo", tenant_id="broadpeak-demo", audit_id=audit.id,
            observation_id=voice_ob.id, question="Attach a targeted view of the entrance post.",
            why_needed="PHOTO_RECOMMENDED: A targeted photo strengthens this report but does not itself prove a violation.",
            options=[], answer="Attached as ob_showcase_photo_security", status="ANSWERED",
            created_at=visited + timedelta(minutes=5), updated_at=visited + timedelta(minutes=8)),
    ])

    audit.checklist_responses = _checklist_rows(
        db, security_photo_id=security_photo_ob.id, restroom_photo_id=restroom_photo_ob.id,
        security_finding_id=security_finding.id, restroom_finding_id=restroom_finding.id)
    audit.updated_at = now
    for purpose, in_tokens, out_tokens, latency, cost in (
        ("voice_transcription", 310, 72, 1260, .000273),
        ("audit_analysis:investigate", 1420, 38, 1880, .000521),
        ("audit_analysis", 2380, 410, 6230, .001739),
        ("photo_description", 980, 185, 2920, .000757),
    ):
        db.add(ModelCall(id=f"call_showcase_{purpose.replace(':', '_')}", tenant_id="broadpeak-demo",
            audit_id=audit.id, purpose=purpose, provider="fixture", model="showcase-fixture",
            input_tokens=in_tokens, output_tokens=out_tokens, latency_ms=latency,
            est_cost_usd=cost, ok=True, created_at=visited, updated_at=visited))
    for event, actor, detail in (
        ("SHOWCASE_CAPTURE_COMPLETE", "Field Consultant (DEMO)", {"multimodal": True, "checklist_items": len(audit.checklist_responses)}),
        ("SHOWCASE_HUMAN_REVIEW_COMPLETE", "Reviewer (DEMO)", {"approved": 2, "rejected": 1}),
        ("SUBMITTED", "Field Consultant (DEMO)", {"showcase": True, "open_questions": 0}),
    ):
        db.add(AuditLog(id=f"log_showcase_{event.lower()}", tenant_id="broadpeak-demo", actor=actor,
                        entity_type="audit", entity_id=audit.id, event=event, detail=detail,
                        created_at=visited, updated_at=visited))
    db.commit()


def replace_audits_with_showcase() -> dict:
    """Delete audit-domain test data only, preserve signals/taxonomy/access data."""
    db = SessionLocal()
    audits = db.query(AuditSession).all()
    audit_ids = {row.id for row in audits}
    observations = db.query(Observation).filter(Observation.audit_id.in_(audit_ids)).all() if audit_ids else []
    findings = db.query(Finding).filter(Finding.audit_id.in_(audit_ids)).all() if audit_ids else []
    questions = db.query(ClarificationQuestion).filter(
        ClarificationQuestion.audit_id.in_(audit_ids)).all() if audit_ids else []
    observation_ids = {row.id for row in observations}
    finding_ids = {row.id for row in findings}
    actions = db.query(Action).filter(Action.finding_id.in_(finding_ids)).all() if finding_ids else []
    action_ids = {row.id for row in actions}
    related_refs = audit_ids | observation_ids | finding_ids | action_ids
    tickets = [row for row in db.query(OperationalTicket).all()
               if related_refs.intersection(row.source_refs or [])]
    evidence_ids = {evidence_id for finding in findings for evidence_id in (finding.evidence_ids or [])}
    evidence = [row for row in db.query(EvidenceItem).all()
                if row.id in evidence_ids
                or (row.payload or {}).get("observation_id") in observation_ids
                or (row.payload or {}).get("audit_id") in audit_ids]
    digests = {digest for row in evidence for digest in (
        (row.payload or {}).get("image_sha256"), (row.payload or {}).get("media_sha256")) if digest}
    entity_ids = related_refs | {row.id for row in questions} | {row.id for row in tickets}
    for row in actions + tickets + findings + questions + observations + evidence:
        db.delete(row)
    if audit_ids:
        db.query(ModelCall).filter(ModelCall.audit_id.in_(audit_ids)).delete(synchronize_session=False)
    if entity_ids:
        db.query(AuditLog).filter(AuditLog.entity_id.in_(entity_ids)).delete(synchronize_session=False)
    for row in audits:
        db.delete(row)
    db.commit()
    for digest in digests:
        for path in config.UPLOADS_DIR.glob(f"{digest}.*"):
            if path.is_file():
                path.unlink()
    seed_showcase(db)
    result = {"removed_audits": len(audits), "showcase_audit_id": SHOWCASE_AUDIT_ID}
    db.close()
    return result


if __name__ == "__main__":
    print(replace_audits_with_showcase())
