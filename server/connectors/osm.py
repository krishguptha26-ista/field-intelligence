"""OpenStreetMap connector — keyless, free, and independent of any vendor console.

Why this exists: the Google Places dependency is a single point of failure that
sits outside our control. During this build it was switched off at the project
level and there was nothing in the code that could fix it. OSM answers the half
of the problem that matters most for evidence — *is this the right place, and
what does an independent source say about it* — with no key, no billing account,
and no quota.

What it can do: entity resolution (name, address, coordinates), and independent
facts about the location (phone, website, category) that can be cross-checked
against what the operator publishes about themselves.

What it cannot do: reviews. OSM has none, and no free source does. That is a
real limitation, stated rather than papered over.

Licence: data is ODbL 1.0. Attribution is carried in every result and rendered
in the UI, because attribution is a licence condition, not a courtesy.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx

from .. import config

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy requires a User-Agent that identifies the application
# and a contact. Sending a generic client string is how projects get blocked.
_UA = {"User-Agent": (f"FieldIntelligence/{config.APP_VERSION} "
                      f"(field-audit POC; +{config.CONTACT_URL})")}

ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"

# Nominatim asks for at most one request per second. One in-process gate is
# enough for a single-node POC; a distributed deployment needs a shared limiter,
# which is noted in ADR-010 rather than pretended away here.
_last_call = 0.0
_MIN_INTERVAL = 1.1

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 86400  # place facts move on the order of months, not minutes


def _throttle() -> None:
    global _last_call
    wait = _MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def lookup_place(name: str, address: str) -> dict:
    """Resolve a location against OSM. Returns a provenance-labelled result.

    Queries widen from precise to loose. Observed during testing: the full
    "name, street, city, state" string returns nothing for Wolf Creek, while
    "Wolf Creek Golf Course Atlanta GA" returns the exact relation — Nominatim
    matches names as recorded in OSM, which are not always the operator's own
    trading name. That mismatch is itself worth surfacing (see `name_variance`).
    """
    key = f"{name}|{address}"
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return {**hit[1], "provenance": "CACHED_LIVE_DATA"}

    city_state = ", ".join(address.split(",")[-2:]).strip() if "," in address else ""
    attempts = [
        f"{name}, {address}",
        f"{name} {city_state}".strip(),
        f"{name.replace('Club', 'Course')} {city_state}".strip(),
    ]
    errors: list[str] = []
    for q in attempts:
        try:
            _throttle()
            r = httpx.get(NOMINATIM_URL, timeout=20, headers=_UA,
                          params={"q": q, "format": "jsonv2", "extratags": 1,
                                  "addressdetails": 1, "limit": 3})
            r.raise_for_status()
            results = r.json()
        except Exception as e:
            errors.append(f"{q!r}: {type(e).__name__}")
            continue
        if not results:
            continue

        best = results[0]
        tags = best.get("extratags") or {}
        out = {
            "ok": True,
            "provenance": "LIVE_API",
            "source": "openstreetmap",
            "attribution": ATTRIBUTION,
            "matched_query": q,
            "osm_type": best.get("osm_type"),
            "osm_id": best.get("osm_id"),
            "osm_url": (f"https://www.openstreetmap.org/"
                        f"{best.get('osm_type')}/{best.get('osm_id')}"),
            "name": best.get("name"),
            "category": f"{best.get('category')}/{best.get('type')}",
            "display_name": best.get("display_name"),
            "lat": float(best["lat"]) if best.get("lat") else None,
            "lng": float(best["lon"]) if best.get("lon") else None,
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "website": tags.get("website") or tags.get("contact:website"),
            "opening_hours": tags.get("opening_hours"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            # The operator's trading name vs the name recorded in OSM. A
            # difference is not an error — it is a cross-channel inconsistency
            # of exactly the kind the digital-truth card exists to surface.
            "name_variance": (None if (best.get("name") or "").strip().lower() == name.strip().lower()
                              else {"operator_name": name, "osm_name": best.get("name")}),
            "address_confirms": _address_overlap(address, best.get("display_name") or ""),
        }
        _cache[key] = (time.time(), out)
        return out

    return {"ok": False, "provenance": "NONE", "source": "openstreetmap",
            "attribution": ATTRIBUTION,
            "error": "no OSM match for any query variant",
            "attempts": attempts, "errors": errors}


def _address_overlap(seeded: str, osm_display: str) -> dict:
    """Independent confirmation that we are looking at the same physical place.

    Entity resolution by name alone is how an audit ends up attached to a
    different business with a similar name. Matching the street number and
    street name against a second, unrelated source is cheap insurance.
    """
    import re
    a = set(re.findall(r"[a-z0-9]+", seeded.lower()))
    b = set(re.findall(r"[a-z0-9]+", osm_display.lower()))
    number = next((t for t in a if t.isdigit() and len(t) >= 3), None)
    shared = sorted(a & b - {"rd", "sw", "st", "ave"})
    return {
        "street_number_matches": bool(number and number in b),
        "shared_tokens": shared[:8],
        "verdict": ("CONFIRMED" if number and number in b and len(shared) >= 3
                    else "WEAK" if shared else "NO_OVERLAP"),
    }
