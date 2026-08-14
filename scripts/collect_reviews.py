"""One-off collector for the FULL public review set of a location.

Why this exists: the Places API returns at most five Google-selected reviews.
Wolf Creek has 362 ratings averaging 4.0, so those five are neither complete nor
representative — during testing the API returned four 5-star reviews and one
2-star, while the listing's own histogram shows a clear band of 1- and 2-star
ratings. Five reviews cannot tell you what a location's customers complain
about.

This is deliberately NOT part of the running application:

  * It is a script an operator runs consciously, not a service the app calls.
  * It needs a signed-in browser session. Google serves a stripped panel with no
    Reviews tab at all to a signed-out browser — verified in both headless and
    real headed Chrome. That is the gate, not the URL and not headless mode.
  * Output is written to the scraper cache, which the app reads only when
    ENABLE_SCRAPED_SIGNALS=true, and which lands at the BOTTOM of the trust
    ladder (ADR-010). More data does not mean more trust.

Reviews remain context and never proof, regardless of how many there are.

Usage
-----
    python scripts/collect_reviews.py --location wolf-creek-atlanta

First run opens a browser window. Sign in to Google once; the profile is kept in
var/collector_profile so later runs skip it. The script waits for the reviews
pane, then scrolls and extracts.

Read the terms of the site you point this at before you run it, and do not run
it against a listing you have no business collecting.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROFILE_DIR = ROOT / "var" / "collector_profile"
CACHE_DIR = ROOT / "var" / "cache"

# Relative ages ("3 months ago") are what the DOM exposes. Converted to a day
# count so the ~92-day recency window can be applied; approximate by design and
# labelled as such rather than presented as a timestamp.
_AGE = re.compile(r"(?:(a|an|\d+)\s+)?(minute|hour|day|week|month|year)s?\s+ago", re.I)
_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


def _age_days(text: str) -> int | None:
    m = _AGE.search(text or "")
    if not m:
        return None
    qty = m.group(1)
    n = 1 if qty in (None, "a", "an") else int(qty)
    return n * _UNIT_DAYS[m.group(2).lower()]


def collect(location_id: str, place_url: str, max_minutes: float,
            wait_minutes: float = 15.0) -> dict:
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), headless=False, channel="chrome",
            locale="en-US", viewport={"width": 1400, "height": 950})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60_000)
        page.goto(place_url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        # Wait for a signed-in session. The Reviews pane simply does not exist
        # for a signed-out visitor, so its appearance is the readiness signal.
        print("\n" + "=" * 68)
        print("  A Chrome window has opened. Do this in it:")
        print("    1. Click 'Sign in' (top right) and sign in to Google.")
        print("    2. You should land back on Wolf Creek Golf Club with a")
        print("       'Reviews' tab next to 'Overview'. Click it if it does not")
        print("       open by itself.")
        print("  Collection starts automatically once the reviews are visible.")
        print("  Leave the window open and do not close it.")
        print("=" * 68 + "\n")
        deadline = time.time() + wait_minutes * 60
        found = False
        last_note = 0.0
        while time.time() < deadline:
            try:
                if page.locator('div[data-review-id]').count() > 0:
                    found = True
                    break
            except Exception:
                pass
            if time.time() - last_note > 30:
                left = int((deadline - time.time()) / 60)
                print(f"  ...still waiting for sign-in ({left} min left)")
                last_note = time.time()
            page.wait_for_timeout(3000)

        if not found:
            try:
                shot = ROOT / "var" / "collector_timeout.png"
                page.screenshot(path=str(shot))
                print(f"  screenshot of what the browser saw: {shot}")
            except Exception:
                pass
            ctx.close()
            raise SystemExit(
                "Reviews pane never appeared. Google shows no Reviews tab at all to a "
                "signed-out visitor, so this almost always means sign-in did not complete. "
                "Re-run and sign in when the window opens.")

        print("Reviews pane is up. Collecting...")

        stop_at = time.time() + max_minutes * 60
        last, stagnant = 0, 0
        while time.time() < stop_at:
            # Scroll the review list from inside the page: find the deepest
            # scrollable container and drive it, which is what actually triggers
            # Google's lazy loader.
            page.evaluate("""() => {
                const panes = [...document.querySelectorAll('div')]
                  .filter(d => d.scrollHeight > d.clientHeight + 100 && d.clientHeight > 300);
                const p = panes[panes.length - 1];
                if (p) p.scrollTop = p.scrollHeight;
            }""")
            page.wait_for_timeout(900)
            n = page.locator('div[data-review-id]').count()
            if n == last:
                stagnant += 1
                if stagnant >= 8:
                    break
            else:
                stagnant = 0
                if n // 50 != last // 50:
                    print(f"  {n} loaded...")
            last = n

        cards = page.locator('div[data-review-id]')
        total = cards.count()
        print(f"Extracting {total} reviews...")
        for i in range(total):
            c = cards.nth(i)
            try:
                text = c.inner_text() or ""
                rid = c.get_attribute("data-review-id") or f"idx{i}"
                rating = None
                star = c.locator('span[role="img"][aria-label*="star"]')
                if star.count():
                    lab = star.first.get_attribute("aria-label") or ""
                    head = lab.strip().split(" ")[0].replace(",", ".")
                    if head.replace(".", "").isdigit():
                        rating = float(head)
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                author = lines[0] if lines else ""
                body = " ".join(l for l in lines[1:]
                                if not _AGE.fullmatch(l) and "review" not in l.lower()[:12])
                out.append({
                    "id": f"scr_{rid[:28]}",
                    "rating": rating,
                    "text": body[:1500],
                    "author": author,
                    "days_ago": _age_days(text),
                    "provenance": "SCRAPED_PUBLIC_WEB",
                })
            except Exception:
                continue
        ctx.close()

    payload = {
        "location_id": location_id,
        "reviews": out,
        "note": (f"Collected {len(out)} public reviews from the location's public listing. "
                 "Lowest trust class; context only, never proof. Ages are approximate, "
                 "derived from relative labels shown on the page."),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "collector": "scripts/collect_reviews.py (signed-in Chrome, manual run)",
        "author_names_collected": True,
    }
    path = CACHE_DIR / f"scraped_reviews_{location_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"count": len(out), "path": str(path)}


DEFAULT_URLS = {
    "wolf-creek-atlanta": (
        "https://www.google.com/maps/place/Wolf+Creek+Golf+Club/@33.6801284,-84.5802555,17z/"
        "data=!4m8!3m7!1s0x88f51fdecc67d089:0x5dff60348e2f1dd6!8m2!3d33.6801284!4d-84.5802555"
        "!9m1!1b1!16s%2Fg%2F1tdbg8bc?hl=en"),
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--location", default="wolf-creek-atlanta")
    ap.add_argument("--url", default=None,
                    help="Reviews deep link. The '!9m1!1b1' segment opens the reviews pane.")
    ap.add_argument("--minutes", type=float, default=8.0, help="scroll budget")
    ap.add_argument("--wait", type=float, default=15.0,
                    help="minutes to wait for you to sign in")
    a = ap.parse_args()
    url = a.url or DEFAULT_URLS.get(a.location)
    if not url:
        raise SystemExit(f"no default URL for {a.location}; pass --url")
    r = collect(a.location, url, a.minutes, a.wait)
    print(f"\nCollected {r['count']} reviews -> {r['path']}")
    print("Set ENABLE_SCRAPED_SIGNALS=true in .env for the app to read them.")
