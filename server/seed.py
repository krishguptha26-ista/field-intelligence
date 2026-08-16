"""Seed data: two fixture tenants proving the multi-tenant engine.

Fixtures remain labelled DEMO_FIXTURE / REPRESENTATIVE_DEMO_STANDARD. Wolf
Creek's public identity and external checklist sources are separately labelled;
BroadPeak has not supplied its controlled internal standards (requested by email
12 Aug).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import (Action, AuditSession, ExternalSignal, Finding, Location,
                     OperationalTicket, SessionLocal, Standard, Tenant, Zone,
                     init_db, uid)
from .regulatory import WOLF_CREEK_STANDARD_DEFS


def _std(tenant: str, cat: str, code: str, text: str, sev: str = "MEDIUM",
         source_label: str = "REPRESENTATIVE_DEMO_STANDARD") -> Standard:
    return Standard(id=uid("req"), tenant_id=tenant, category=cat, code=code,
                    text=text, severity_default=sev, source_label=source_label)


def _sync_wolf_creek_pack(db) -> None:
    """Add missing sourced checks; published versions are never rewritten in place."""
    tenant = db.get(Tenant, "broadpeak-demo")
    location = db.get(Location, "wolf-creek-atlanta")
    if tenant is None or location is None:
        return
    for definition in WOLF_CREEK_STANDARD_DEFS:
        row = db.query(Standard).filter_by(
            tenant_id=tenant.id, code=definition["code"]
        ).first()
        values = {
            "category": definition["category"],
            "text": definition["text"],
            "severity_default": definition["severity"],
            "source_label": definition["source_label"],
            "active": True,
        }
        if row is None:
            db.add(Standard(id=uid("req"), tenant_id=tenant.id,
                            code=definition["code"], **values))
    if db.query(Zone).filter_by(
        location_id=location.id, name="Maintenance & chemical storage"
    ).first() is None:
        db.add(Zone(id=uid("zone"), tenant_id=tenant.id, location_id=location.id,
                    name="Maintenance & chemical storage", required=True,
                    privacy_level="NORMAL"))
    db.commit()


def _migrate_photo_attachment_labels(db) -> None:
    """Narrow legacy wording that implied an attached photo proved the issue."""
    changed = False
    for audit in db.query(AuditSession).all():
        rows = list(audit.checklist_responses or [])
        next_rows = []
        for row in rows:
            if row.get("verification_state") == "PHOTO_SUPPORTED":
                row = {**row, "verification_state": "PHOTO_ATTACHED_PENDING_REVIEW"}
                changed = True
            next_rows.append(row)
        if next_rows != rows:
            audit.checklist_responses = next_rows
    if changed:
        db.commit()


def _migrate_legacy_representative_disclaimer(db) -> None:
    """Remove the retired, needlessly defensive sentence from saved records."""
    legacy = (
        "Representative demo guidance only; BroadPeak did not supply an "
        "authoritative standard or confirm applicability."
    )
    replacement = "Operating-guide comparison for human review; not a legal conclusion."
    changed = False
    for model, field in (
        (Finding, "model_interpretation"),
        (Action, "description"),
        (OperationalTicket, "description"),
    ):
        for row in db.query(model).all():
            current = getattr(row, field) or ""
            if legacy in current:
                setattr(row, field, current.replace(legacy, replacement))
                changed = True
    if changed:
        db.commit()


def seed() -> None:
    init_db()
    db = SessionLocal()
    if db.query(Tenant).count() > 0:
        _sync_wolf_creek_pack(db)
        _migrate_photo_attachment_labels(db)
        _migrate_legacy_representative_disclaimer(db)
        _seed_history(db)   # additive: back-fills history into an existing demo db
        db.close()
        return

    now = datetime.now(timezone.utc)

    # ---------------- Tenant 1: Wolf Creek (golf) ----------------
    t1 = Tenant(id="broadpeak-demo", name="BroadPeak Sports & Entertainment — Golf", kind="venue")
    l1 = Location(id="wolf-creek-atlanta", tenant_id=t1.id, name="Wolf Creek Golf Club",
                  address="3000 Union Rd SW, Atlanta, GA 30331",
                  lat=33.6801284, lng=-84.5802555,
                  meta={"entity_resolution": "name+address; Place ID persisted on first live lookup"})
    zones1 = ["Arrival & entrance signage", "Parking / accessible parking", "Clubhouse exterior",
              "Lobby / check-in", "Pro shop", "Restrooms", "Food & beverage area",
              "Cart staging", "Driving range", "Starter / first tee", "On-course facilities",
              "18th hole / departure"]
    stds1 = [
        _std(t1.id, "cleanliness", "CLN-01", "Restrooms are cleaned and inspected on a posted schedule; free of debris, odours, standing water and overflowing waste at all times during operating hours.", "HIGH"),
        _std(t1.id, "cleanliness", "CLN-02", "Interior public floors are free of spills, litter and slip hazards; wet-floor signage used during cleaning."),
        _std(t1.id, "safety", "SAF-01", "Cart paths and walkways are free of trip hazards, damaged surfaces and unmarked obstructions.", "HIGH"),
        _std(t1.id, "safety", "SAF-02", "Chemical and maintenance materials are stored secured, labelled and inaccessible to guests.", "CRITICAL"),
        _std(t1.id, "signage", "SIG-01", "Wayfinding and regulatory signage is present, legible, undamaged and current from arrival road through first tee."),
        _std(t1.id, "operations", "OPS-01", "Check-in wait does not exceed 5 minutes at posted staffing levels; queue overflow procedure is in use when exceeded."),
        _std(t1.id, "operations", "OPS-02", "Pace-of-play monitoring is active; intervals per the daily tee sheet; deviations logged with cause."),
        _std(t1.id, "food_safety", "FNB-01", "Food-contact surfaces cleaned and sanitised per schedule; temperature logs current for hot/cold holding.", "CRITICAL"),
        _std(t1.id, "course_condition", "CRS-01", "Greens, tees and fairways maintained per agronomy plan; hazards (standing water, damage) flagged and communicated to the starter."),
    ] + [
        _std(t1.id, item["category"], item["code"], item["text"],
             item["severity"], item["source_label"])
        for item in WOLF_CREEK_STANDARD_DEFS
    ]

    # ---------------- Tenant 2: EV & Delivery depot (mobility) ----------------
    t2 = Tenant(id="broadpeak-mobility-demo", name="BroadPeak Mobility (Illustrative)", kind="mobility")
    l2 = Location(id="alquoz-depot-dubai", tenant_id=t2.id, name="Al Quoz EV & Delivery Depot",
                  address="Al Quoz Industrial Area, Dubai, UAE (DEMO_FIXTURE)",
                  meta={"fixture": True, "note": "Illustrative second tenant proving the multi-tenant engine"})
    zones2 = ["Charging bays", "Battery storage room", "Staging & dispatch", "Driver rest area",
              "Customer handover point", "Yard & perimeter"]
    stds2 = [
        _std(t2.id, "safety", "EVS-01", "Charging cables are racked when idle; no cable crosses a walkway or drive lane uncovered.", "HIGH"),
        _std(t2.id, "safety", "EVS-02", "Battery storage room: access controlled, temperature within range, fire suppression unobstructed.", "CRITICAL"),
        _std(t2.id, "equipment", "EVE-01", "Charger units operational and displaying status; faults reported within 2 hours of detection.", "HIGH"),
        _std(t2.id, "signage", "EVG-01", "Bay numbering, PPE and emergency signage present, legible and current."),
        _std(t2.id, "operations", "EVO-01", "Dispatch SLA board reflects live queue; discrepancies logged with cause."),
        _std(t2.id, "cleanliness", "EVC-01", "Bays and handover point free of debris, spills and obstructions."),
    ]

    # Explicit dependency order matters because the POC models deliberately do
    # not carry heavy ORM relationship machinery. Foreign-key enforcement is on
    # in SQLite, so do not rely on SQLAlchemy inferring order from scalar IDs.
    db.add_all([t1, t2])
    db.commit()
    db.add_all([l1, l2])
    db.commit()
    for i, z in enumerate(zones1):
        db.add(Zone(id=f"z1_{i:02d}", tenant_id=t1.id, location_id=l1.id, name=z,
                    privacy_level="HIGH" if "Restroom" in z else "NORMAL"))
    for i, z in enumerate(zones2):
        db.add(Zone(id=f"z2_{i:02d}", tenant_id=t2.id, location_id=l2.id, name=z,
                    privacy_level="HIGH" if "rest" in z.lower() else "NORMAL"))
    db.add_all(stds1 + stds2)
    db.commit()

    # ---------------- Fixture review sample (used when no Maps key / offline) ----------------
    fixture_reviews = [
        (2, "Pace of play was brutal on Saturday — almost six hours. Marshals nowhere to be seen.", "K. D.", 21),
        (3, "Course itself is a great layout but the men's restroom by the clubhouse needed attention both times I went in.", "R. Patel", 35),
        (1, "Waited 25 minutes at check-in because one register was open. Round itself was fine.", "M. Alvarez", 49),
        (5, "Fantastic championship track, greens rolled true. Will be back.", "J. Chen", 8),
        (2, "Slow round again... beverage cart came by once in 18 holes.", "T. Brooks", 62),
    ]
    for rating, text, author, days_ago in fixture_reviews:
        db.add(ExternalSignal(
            id=uid("sig"), tenant_id=t1.id, location_id=l1.id,
            signal_type="GOOGLE_REVIEW", rating=rating, text=text, author=author,
            published_at=now - timedelta(days=days_ago),
            provenance="DEMO_FIXTURE",
            payload={"label": "Google-selected sample; maximum five; not statistically representative",
                     "fixture_reason": "used when GOOGLE_MAPS_API_KEY absent or offline"}))

    db.commit()
    _sync_wolf_creek_pack(db)
    _migrate_photo_attachment_labels(db)
    _migrate_legacy_representative_disclaimer(db)
    _seed_history(db)
    db.close()


def _seed_history(db) -> None:
    """Seed the curated showcase plus its hidden recurrence support row."""
    from .showcase import seed_showcase
    seed_showcase(db)


if __name__ == "__main__":
    seed()
    print("seeded")
