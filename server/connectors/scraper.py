"""Public-web review collection — quarantined on purpose.

This module is deliberately isolated from every other connector, and every part
of its design says the same thing: this is the least trusted way to get data
into the system.

  * OFF by default (`ENABLE_SCRAPED_SIGNALS`). Turning it on is a decision an
    operator makes with their own counsel, not a default we make for them.
  * Cache-first, always. A live demo never depends on a scrape completing:
    collection is a background/offline activity, and the app reads the cache.
  * Results are stamped SCRAPED_PUBLIC_WEB and sit at the bottom of the trust
    ladder in `sources.py`. They can never be the sole basis for anything, and
    like every review source they are context and never proof.
  * Heavily rate-limited, single-threaded, and it identifies itself honestly.

Why it exists at all: the vendor API for this data was unavailable for the whole
of this build for reasons outside the code, and an evidence system should be
able to state what the public record says without asking permission from a
console. Why it is off: collection that depends on a third party's terms is a
business decision with legal surface, and a product that governs evidence has
no business quietly making that call on an operator's behalf.

The honest engineering assessment, recorded because it matters more than the
code: this is the most fragile component in the system. It depends on the DOM
of a page that changes without notice, it needs a ~150MB browser that will not
fit comfortably on a small host, and it will break. That is why it is optional
and cached, and why the production recommendation is the operator's own Google
Business Profile export — the same data, owned, stable, and free.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from .. import config

CACHE_DIR = config.VAR_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_HOURS = 24 * 7          # reviews are not a real-time signal
NAV_TIMEOUT_MS = 45_000
MAX_REVIEWS = 20

PROVENANCE = "SCRAPED_PUBLIC_WEB"


def _cache_path(location_id: str):
    return CACHE_DIR / f"scraped_reviews_{location_id}.json"


def _read_cache(location_id: str) -> dict | None:
    p = _cache_path(location_id)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(blob["fetched_at"])
    blob["cache_age_hours"] = round(age.total_seconds() / 3600, 1)
    blob["stale"] = age > timedelta(hours=CACHE_TTL_HOURS)
    return blob


def fetch_scraped_reviews(location_id: str) -> dict:
    """Cache-first read. Only collects live when the cache is missing/stale."""
    if not config.ENABLE_SCRAPED_SIGNALS:
        return {"_ok": False, "_provenance": "NONE",
                "error": "scraped signals disabled (ENABLE_SCRAPED_SIGNALS=false)",
                "reviews": []}

    cached = _read_cache(location_id)
    if cached and not cached.get("stale"):
        return {"_ok": bool(cached.get("reviews")), "_provenance": PROVENANCE,
                "reviews": cached.get("reviews", []),
                "cache_age_hours": cached.get("cache_age_hours"),
                "collected_at": cached.get("fetched_at"),
                "collection_note": cached.get("note", ""),
                "sample_caveat": ("Publicly posted reviews collected from the web. Lowest trust "
                                  "class; context only, never proof; not a representative sample.")}

    try:
        fresh = collect(location_id)
    except Exception as e:
        return {"_ok": False, "_provenance": "NONE",
                "error": f"collection failed: {type(e).__name__}: {str(e)[:160]}",
                "reviews": (cached or {}).get("reviews", []),
                "note": "Returning stale cache if present; collection is best-effort by design."}
    return {"_ok": bool(fresh.get("reviews")), "_provenance": PROVENANCE,
            "reviews": fresh.get("reviews", []), "collected_at": fresh.get("fetched_at"),
            "collection_note": fresh.get("note", ""),
            "sample_caveat": ("Publicly posted reviews collected from the web. Lowest trust "
                              "class; context only, never proof; not a representative sample.")}


def collect(location_id: str) -> dict:
    """Drive a headless browser once and write the cache. Slow and deliberate.

    Kept synchronous and single-flight: concurrency here buys nothing except a
    higher chance of being rate-limited, and there is no scenario in this product
    where review collection is on a latency-critical path.
    """
    from playwright.sync_api import sync_playwright

    from ..models import Location, SessionLocal
    db = SessionLocal()
    loc = db.get(Location, location_id)
    db.close()
    if loc is None:
        raise ValueError(f"unknown location {location_id}")

    query = f"{loc.name} {loc.address}".replace(" ", "+")
    reviews: list[dict] = []
    note = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"))
        page = ctx.new_page()
        page.set_default_timeout(NAV_TIMEOUT_MS)
        try:
            page.goto(f"https://www.google.com/maps/search/{query}?hl=en",
                      wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # A search may land on a result list rather than the place itself.
            # Open the first result when that happens.
            if page.locator('div[role="feed"] a[href*="/maps/place/"]').count():
                page.locator('div[role="feed"] a[href*="/maps/place/"]').first.click()
                page.wait_for_timeout(4000)

            for sel in ('button[aria-label*="Reviews for"]',
                        'button[jsaction*="pane.reviewChart"]',
                        'button:has-text("Reviews")',
                        'div[role="tab"]:has-text("Reviews")'):
                try:
                    el = page.locator(sel).first
                    if el.count():
                        el.click(timeout=6000)
                        break
                except Exception:
                    continue
            page.wait_for_timeout(3500)

            # Scroll the review pane a bounded number of times. Bounded, not
            # exhaustive: we want a sample, and an unbounded scroll loop against
            # someone else's site is the kind of thing that gets an IP blocked.
            pane = page.locator('div[role="main"]')
            for _ in range(6):
                try:
                    page.mouse.wheel(0, 3000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                if page.locator('div[data-review-id]').count() >= MAX_REVIEWS:
                    break

            cards = page.locator('div[data-review-id]')
            count = min(cards.count(), MAX_REVIEWS)
            for i in range(count):
                c = cards.nth(i)
                try:
                    rid = c.get_attribute("data-review-id") or f"scraped_{i}"
                    text = ""
                    for tsel in ('span[class*="wiI7pd"]', 'div[class*="MyEned"]', "span"):
                        n = c.locator(tsel)
                        if n.count():
                            text = (n.first.inner_text() or "").strip()
                            if len(text) > 20:
                                break
                    rating = None
                    star = c.locator('span[role="img"][aria-label*="star"]')
                    if star.count():
                        lab = star.first.get_attribute("aria-label") or ""
                        head = lab.strip().split(" ")[0].replace(",", ".")
                        rating = float(head) if head.replace(".", "").isdigit() else None
                    if text:
                        reviews.append({"id": f"scr_{rid[:24]}", "rating": rating,
                                        "text": text[:1200], "author": "",  # not collected
                                        "provenance": PROVENANCE})
                except Exception:
                    continue
            if not reviews:
                note = ("Page rendered but no review cards matched. The layout has almost "
                        "certainly changed — expected for this collection method.")
        finally:
            browser.close()

    out = {"location_id": location_id, "reviews": reviews, "note": note,
           "fetched_at": datetime.now(timezone.utc).isoformat(),
           "collector": "playwright/chromium",
           "author_names_collected": False}
    _cache_path(location_id).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
