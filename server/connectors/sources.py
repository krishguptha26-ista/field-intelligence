"""Signal source registry — many sources, fanned out in parallel, ranked by trust.

The problem this solves showed up during the build rather than in the design.
The Google Places dependency went down for reasons entirely outside the code
(an API disabled at project level, then a key restriction list), and there was
nothing an engineer could do about it from inside the application. A product
whose evidence layer can be switched off by someone else's console setting is
not a product; it is a demo waiting to fail.

So sources are plural, queried concurrently, and ranked:

    OPERATOR_OWNED   the operator's own export of their own listing. Highest
                     trust: they own the data and can vouch for it. This is
                     what a real deployment uses (BroadPeak owns these listings
                     through Google Business Profile).
    OFFICIAL_API     a vendor API under contract — Google Places. Trustworthy
                     and rate-limited and revocable.
    OPEN_DATA        OpenStreetMap. Free, keyless, independently maintained.
                     Excellent for entity resolution; has no reviews at all.
    SCRAPED_WEB      public web collection. Lowest trust, OFF by default, and
                     never the sole basis for anything. See ADR-010.

Two rules hold regardless of source:

  * Trust rank never converts sentiment into proof. A five-star review from the
    highest-trust source is still context. The ladder ranks *how much we believe
    the data is what it claims to be*, not *what it can be used for*.
  * A failing source degrades the result; it never breaks the request. The fan
    -out returns partial results with each source's status attached, because
    "three of four sources answered" is information a reviewer should see.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .. import config

TRUST_RANK = {"OPERATOR_OWNED": 4, "OFFICIAL_API": 3, "OPEN_DATA": 2, "SCRAPED_WEB": 1}


@dataclass
class SourceResult:
    source_id: str
    trust_class: str
    ok: bool
    provenance: str                      # LIVE_API|CACHED_LIVE_DATA|DEMO_FIXTURE|SCRAPED_PUBLIC_WEB|NONE
    kind: str                            # "reviews" | "place_facts"
    data: dict = field(default_factory=dict)
    error: str = ""
    attribution: str = ""
    latency_ms: int = 0
    fetched_at: str = ""

    def as_dict(self) -> dict:
        return {"source_id": self.source_id, "trust_class": self.trust_class,
                "trust_rank": TRUST_RANK.get(self.trust_class, 0), "ok": self.ok,
                "provenance": self.provenance, "kind": self.kind, "data": self.data,
                "error": self.error, "attribution": self.attribution,
                "latency_ms": self.latency_ms, "fetched_at": self.fetched_at}


@dataclass
class Source:
    source_id: str
    trust_class: str
    kind: str
    enabled: Callable[[], bool]
    fetch: Callable[[str], dict]
    attribution: str = ""
    note: str = ""


# ---------------------------------------------------------------------------
# Source implementations
# ---------------------------------------------------------------------------

def _places_reviews(location_id: str) -> dict:
    from .places import fetch_review_sample
    s = fetch_review_sample(location_id)
    live = s.get("provenance") == "LIVE_API"
    return {"_provenance": s.get("provenance", "DEMO_FIXTURE"),
            "_ok": True, "reviews": s.get("reviews", []),
            "sample_caveat": s.get("sample_caveat"),
            "window_days": s.get("window_days"),
            "note": ("" if live else
                     "Places API did not answer; showing the cached/fixture twin. "
                     "Labelled, never presented as live.")}


def _osm_place_facts(location_id: str) -> dict:
    from ..models import Location, SessionLocal
    from .osm import ATTRIBUTION, lookup_place
    db = SessionLocal()
    loc = db.get(Location, location_id)
    db.close()
    if loc is None:
        return {"_ok": False, "_provenance": "NONE", "error": "unknown location"}
    r = lookup_place(loc.name, loc.address)
    return {"_ok": bool(r.get("ok")), "_provenance": r.get("provenance", "NONE"),
            "_attribution": ATTRIBUTION, **r}


def _scraped_reviews(location_id: str) -> dict:
    from .scraper import fetch_scraped_reviews
    return fetch_scraped_reviews(location_id)


def _assessment_snapshot(location_id: str) -> dict:
    from .review_snapshot import load_review_snapshot
    snapshot = load_review_snapshot(location_id)
    if snapshot is None:
        return {"_ok": False, "_provenance": "NONE",
                "error": "no assessment snapshot for this location", "reviews": []}
    return {"_ok": True, "_provenance": snapshot["provenance"], **snapshot}


def _operator_reviews(location_id: str) -> dict:
    """Reviews the operator supplied about their own location.

    Empty until someone uploads an export. That emptiness is the honest state
    of a POC that has not been given access to BroadPeak's own Business Profile
    data — and it is the single highest-value input the product could receive,
    which is why it sits at the top of the ladder with nothing in it.
    """
    from ..models import ExternalSignal, SessionLocal
    db = SessionLocal()
    rows = (db.query(ExternalSignal)
              .filter_by(location_id=location_id, provenance="OPERATOR_UPLOAD")
              .order_by(ExternalSignal.published_at.desc()).limit(50).all())
    db.close()
    return {"_ok": bool(rows), "_provenance": "UPLOADED_DOCUMENT" if rows else "NONE",
            "reviews": [{"id": r.id, "rating": r.rating, "text": r.text,
                         "author": r.author,
                         "published_at": r.published_at.isoformat() if r.published_at else None}
                        for r in rows],
            "error": "" if rows else "no operator export uploaded for this location"}


REGISTRY: list[Source] = [
    Source("operator_export", "OPERATOR_OWNED", "reviews",
           enabled=lambda: True, fetch=_operator_reviews,
           note="The operator's own listing export. Highest trust; the production path."),
    Source("google_places", "OFFICIAL_API", "reviews",
           enabled=lambda: bool(config.GOOGLE_MAPS_API_KEY), fetch=_places_reviews,
           attribution="Google Places API (New)",
           note="Vendor API. Max ~5 Google-selected reviews; not representative."),
    Source("openstreetmap", "OPEN_DATA", "place_facts",
           enabled=lambda: True, fetch=_osm_place_facts,
           attribution="© OpenStreetMap contributors, ODbL 1.0",
           note="Keyless and free. Entity resolution and place facts. No reviews exist in OSM."),
    Source("assessment_snapshot", "SCRAPED_WEB", "reviews",
           enabled=lambda: True, fetch=_assessment_snapshot,
           attribution="One-off public listing snapshot; reviewer identities removed",
           note=("Assessment-only, timestamped snapshot. Complete enough for trend analysis; "
                 "customer context only and never compliance evidence.")),
    Source("scraped_maps", "SCRAPED_WEB", "reviews",
           enabled=lambda: config.ENABLE_SCRAPED_SIGNALS, fetch=_scraped_reviews,
           attribution="Public web collection",
           note="Off by default. Cache-first. Never the sole basis for anything."),
]


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------

def gather(location_id: str, *, timeout: float = 25.0) -> dict:
    """Query every enabled source concurrently; return all outcomes, ranked.

    Concurrent because these are independent network calls with wildly different
    latencies — OSM answers in ~300ms, a browser-backed scrape can take twenty
    seconds — and a reviewer should not wait for the slowest one to learn what
    the fastest three said.
    """
    results: list[SourceResult] = []
    active = [s for s in REGISTRY if s.enabled()]
    skipped = [{"source_id": s.source_id, "trust_class": s.trust_class,
                "reason": "disabled by configuration", "note": s.note}
               for s in REGISTRY if not s.enabled()]

    def run(src: Source) -> SourceResult:
        start = time.time()
        try:
            raw = src.fetch(location_id)
            return SourceResult(
                source_id=src.source_id, trust_class=src.trust_class,
                ok=bool(raw.get("_ok", True)),
                provenance=raw.get("_provenance", "NONE"), kind=src.kind,
                data={k: v for k, v in raw.items() if not k.startswith("_")},
                error=str(raw.get("error", "")),
                attribution=raw.get("_attribution", src.attribution),
                latency_ms=int((time.time() - start) * 1000),
                fetched_at=datetime.now(timezone.utc).isoformat())
        except Exception as e:
            return SourceResult(
                source_id=src.source_id, trust_class=src.trust_class, ok=False,
                provenance="NONE", kind=src.kind,
                error=f"{type(e).__name__}: {str(e)[:180]}",
                attribution=src.attribution,
                latency_ms=int((time.time() - start) * 1000),
                fetched_at=datetime.now(timezone.utc).isoformat())

    if active:
        pool = ThreadPoolExecutor(max_workers=len(active))
        try:
            futures = {pool.submit(run, s): s for s in active}
            try:
                for fut in as_completed(futures, timeout=timeout):
                    results.append(fut.result())
            except FuturesTimeoutError:
                for fut, src in futures.items():
                    if fut.done():
                        continue
                    fut.cancel()
                    results.append(SourceResult(
                        source_id=src.source_id, trust_class=src.trust_class,
                        ok=False, provenance="NONE", kind=src.kind,
                        error=f"source exceeded {timeout:.1f}s request budget",
                        attribution=src.attribution, latency_ms=int(timeout * 1000),
                        fetched_at=datetime.now(timezone.utc).isoformat()))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    results.sort(key=lambda r: (-TRUST_RANK.get(r.trust_class, 0), r.source_id))
    answered = [r for r in results if r.ok]

    # The review set actually used = the highest-trust source that answered.
    # Lower-trust sources are reported but do not silently blend in: mixing a
    # scraped review into an official sample would make the sample's provenance
    # a lie, and provenance is the product.
    review_sources = [r for r in answered if r.kind == "reviews" and r.data.get("reviews")]
    operator = next((r for r in review_sources if r.source_id == "operator_export"), None)
    snapshot = next((r for r in review_sources if r.source_id == "assessment_snapshot"), None)
    # For theme analysis, coverage fitness matters after ownership. A complete,
    # timestamped snapshot is selected ahead of a five-row Places sample, while
    # keeping its lower trust class visible. No sources are blended.
    primary = operator or snapshot or (review_sources[0] if review_sources else None)
    place = next((r for r in answered if r.kind == "place_facts"), None)

    return {
        "location_id": location_id,
        "sources": [r.as_dict() for r in results],
        "skipped": skipped,
        "answered": len(answered),
        "attempted": len(active),
        "primary_review_source": primary.source_id if primary else None,
        "primary_review_provenance": primary.provenance if primary else "NONE",
        "primary_review_selection": (
            "operator-owned export" if operator else
            "coverage-fit assessment snapshot; lower trust remains explicit" if snapshot else
            "highest-trust answering review source" if primary else "no review source"),
        "reviews": (primary.data.get("reviews", []) if primary else []),
        "place_facts": (place.data if place else {}),
        "corroboration": _corroborate(results),
        "trust_ladder": [{"trust_class": k, "rank": v} for k, v in
                         sorted(TRUST_RANK.items(), key=lambda p: -p[1])],
    }


def _corroborate(results: list[SourceResult]) -> dict:
    """Where independent sources agree, disagree, or are silent.

    Agreement between unrelated sources is the cheapest confidence signal
    available, and disagreement is often more interesting than either value
    alone — a name or phone number that differs across channels is exactly the
    externally-visible inconsistency the digital-truth card is for.
    """
    place = next((r for r in results if r.kind == "place_facts" and r.ok), None)
    notes: list[dict] = []
    if place:
        d = place.data
        if d.get("address_confirms", {}).get("verdict") == "CONFIRMED":
            notes.append({"type": "ENTITY_CONFIRMED",
                          "detail": ("Street number and address tokens match an independent "
                                     "OpenStreetMap record — this is the right physical place, "
                                     "confirmed without relying on the vendor we resolve against."),
                          "source": "openstreetmap", "osm_url": d.get("osm_url")})
        if d.get("name_variance"):
            nv = d["name_variance"]
            notes.append({"type": "CROSS_CHANNEL_NAME_VARIANCE",
                          "detail": (f"Trading name differs across channels: the operator uses "
                                     f"\"{nv['operator_name']}\", the public map record says "
                                     f"\"{nv['osm_name']}\". Not a compliance issue — a findable-"
                                     f"ness issue, and a cheap fix."),
                          "source": "openstreetmap"})
    if not any(r.kind == "reviews" and r.ok and r.data.get("reviews") for r in results):
        notes.append({"type": "NO_REVIEW_SOURCE",
                      "detail": ("No review source answered. Findings are unaffected: reviews are "
                                 "context and can never evidence one.")})
    return {"notes": notes}
