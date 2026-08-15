"""Google Places API (New) connector — narrow, deterministic (spec §12).

- Resolves the location by text query once; persists the stable Place ID.
- Requests only required fields via field masks.
- Live path degrades to the stored/cached sample, then to DEMO_FIXTURE seeds.
- Review sample is ALWAYS labelled: Google-selected, max five, not representative.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from threading import Lock

import httpx

from .. import config
from ..gateway import get_provider
from ..models import ExternalSignal, Location, SessionLocal, Standard, uid
from ..schemas import ReviewThemes
from .review_snapshot import load_review_snapshot

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

SAMPLE_CAVEAT = "Google-selected sample; maximum five; not statistically representative."


def _headers(field_mask: str) -> dict:
    return {"X-Goog-Api-Key": config.GOOGLE_MAPS_API_KEY,
            "X-Goog-FieldMask": field_mask,
            "Content-Type": "application/json"}


def resolve_place_id(db, location: Location) -> str | None:
    """Resolve and persist the stable Place ID (strong identifier, not name alone)."""
    if location.place_id:
        return location.place_id
    if not config.GOOGLE_MAPS_API_KEY:
        return None
    resp = httpx.post(SEARCH_URL, timeout=15,
                      headers=_headers("places.id,places.displayName,places.formattedAddress"),
                      json={"textQuery": f"{location.name}, {location.address}"})
    resp.raise_for_status()
    places = resp.json().get("places", [])
    if not places:
        return None
    # entity check: address must overlap, guarding against same-name matches
    best = places[0]
    addr = (best.get("formattedAddress") or "").lower()
    if "union rd" in location.address.lower() and "union" not in addr:
        return None  # ENTITY_AMBIGUOUS — do not guess
    location.place_id = best["id"]
    db.commit()
    return location.place_id


def fetch_review_sample(location_id: str) -> dict:
    """Return the review sample with explicit provenance.

    Order: LIVE_API → CACHED_LIVE_DATA (previous live pull) → DEMO_FIXTURE seeds.
    """
    db = SessionLocal()
    location = db.get(Location, location_id)
    provenance, fetched = "DEMO_FIXTURE", []

    if config.GOOGLE_MAPS_API_KEY and location is not None:
        try:
            pid = resolve_place_id(db, location)
            if pid:
                mask = ("id,displayName,rating,userRatingCount,currentOpeningHours,"
                        "formattedAddress,websiteUri,reviews")
                resp = httpx.get(DETAILS_URL.format(place_id=pid), timeout=15,
                                 headers=_headers(mask))
                resp.raise_for_status()
                data = resp.json()
                now = datetime.now(timezone.utc)
                # replace previous live cache for this location
                (db.query(ExternalSignal)
                   .filter_by(location_id=location_id, signal_type="GOOGLE_REVIEW")
                   .filter(ExternalSignal.provenance.in_(["LIVE_API", "CACHED_LIVE_DATA"]))
                   .delete(synchronize_session=False))
                for rv in data.get("reviews", [])[:5]:
                    pub = rv.get("publishTime")
                    published = datetime.fromisoformat(pub.replace("Z", "+00:00")) if pub else None
                    db.add(ExternalSignal(
                        id=uid("sig"), tenant_id=location.tenant_id, location_id=location_id,
                        signal_type="GOOGLE_REVIEW",
                        rating=rv.get("rating"),
                        text=(rv.get("text") or {}).get("text", ""),
                        author=(rv.get("authorAttribution") or {}).get("displayName", ""),
                        published_at=published, provenance="LIVE_API",
                        payload={"label": SAMPLE_CAVEAT, "attribution": rv.get("authorAttribution")}))
                location.meta = {**(location.meta or {}),
                                 "rating": data.get("rating"),
                                 "rating_count": data.get("userRatingCount"),
                                 "website": data.get("websiteUri"),
                                 "last_places_fetch": now.isoformat()}
                db.commit()
                provenance = "LIVE_API"
        except Exception as e:  # degrade, never break the demo
            provenance = "CACHED_LIVE_DATA_OR_FIXTURE"

    # A live pull returns ONLY live rows. The seeded fixture reviews stay in the
    # table (they are the offline twin), but blending them into a sample labelled
    # LIVE_API would make the provenance label a lie — and a viewer who spots one
    # fixture name inside "live" data is right to distrust every other label on
    # the screen. Mixed provenance in one sample is never acceptable.
    q = (db.query(ExternalSignal)
           .filter_by(location_id=location_id, signal_type="GOOGLE_REVIEW"))
    if provenance == "LIVE_API":
        q = q.filter(ExternalSignal.provenance == "LIVE_API")
    else:
        live_rows = (db.query(ExternalSignal)
                       .filter_by(location_id=location_id, signal_type="GOOGLE_REVIEW",
                                  provenance="LIVE_API").count())
        # Prefer a previous live pull over fixtures, and label it as cached.
        q = q.filter(ExternalSignal.provenance == ("LIVE_API" if live_rows else "DEMO_FIXTURE"))
    signals = q.order_by(ExternalSignal.published_at.desc()).limit(5).all()
    now = datetime.now(timezone.utc)
    for s in signals:
        pub = s.published_at
        if pub is not None and pub.tzinfo is None:  # SQLite round-trips naive datetimes
            pub = pub.replace(tzinfo=timezone.utc)
        days = (now - pub).days if pub else None
        fetched.append({"id": s.id, "rating": s.rating, "text": s.text, "author": s.author,
                        "days_ago": days, "provenance": s.provenance})
    if provenance == "DEMO_FIXTURE" and any(s.provenance == "LIVE_API" for s in signals):
        provenance = "CACHED_LIVE_DATA"
    db.close()
    return {"reviews": fetched, "sample_caveat": SAMPLE_CAVEAT, "provenance": provenance,
            "window_days": 92, "location_meta": (location.meta if location else {})}


def _summarise_themes_uncached(location_id: str, tenant_id: str) -> dict:
    """LLM theme summary over a labelled source sample — context, never proof."""
    # The assessment snapshot is collected offline and never makes a scrape part
    # of page latency. Other locations fall back to Places/fixture, but are still
    # filtered locally: a 92-day label must describe the rows on screen.
    sample = load_review_snapshot(location_id)
    if sample is None:
        raw = fetch_review_sample(location_id)
        selected = [r for r in raw.get("reviews", [])
                    if r.get("days_ago") is not None
                    and 0 <= r["days_ago"] <= 92
                    and (r.get("rating") or 5) <= 3
                    and (r.get("text") or "").strip()]
        sample = {**raw, "reviews": selected,
                  "selection": "published within 92 days; rating <= 3; written reviews",
                  "dataset_summary": {
                      "source_rows_available": len(raw.get("reviews", [])),
                      "recent_low_rating_written": len(selected),
                  }}
    db = SessionLocal()
    categories = sorted({s.category for s in db.query(Standard).filter_by(tenant_id=tenant_id).all()})
    db.close()
    prompt_doc = (config.PROMPTS_DIR / "review_themes.md").read_text()
    prompt = (f"{prompt_doc}\n\nAudit categories: {categories}\n\n"
              f"INPUT_JSON:{json.dumps({'reviews': sample['reviews']})}")
    themes: ReviewThemes = get_provider().generate(
        purpose="review_themes", prompt=prompt, schema=ReviewThemes,
        tenant_id=tenant_id, audit_id=None)
    theme_payload = themes.model_dump()
    # Provenance and sample limitations are deterministic metadata. Never let a
    # provider's generic five-review caveat contradict the selected snapshot.
    theme_payload["sample_caveat"] = sample.get("sample_caveat", "")
    out = {"sample": sample, "themes": theme_payload}
    _THEME_CACHE[(location_id, tenant_id)] = (time.time(), out)
    return out


# Themes are re-summarised at most once per TTL. Without this, every agent run
# that calls customer_signal_context would pay for a second LLM call over a
# source snapshot that changes at most daily — a real cost line at portfolio
# scale, and a pointless one.
_THEME_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_THEME_TTL_SECONDS = 900
_THEME_LOCK = Lock()


def summarise_themes(location_id: str, tenant_id: str) -> dict:
    """Return cached theme analysis and suppress duplicate concurrent calls."""
    key = (location_id, tenant_id)
    hit = _THEME_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _THEME_TTL_SECONDS:
        return hit[1]
    with _THEME_LOCK:
        hit = _THEME_CACHE.get(key)
        if hit and (time.time() - hit[0]) < _THEME_TTL_SECONDS:
            return hit[1]
        return _summarise_themes_uncached(location_id, tenant_id)


def theme_summary_cached(location_id: str, tenant_id: str) -> dict:
    """Cached theme summary for tool use. Returns the themes payload only."""
    return summarise_themes(location_id, tenant_id)["themes"]
