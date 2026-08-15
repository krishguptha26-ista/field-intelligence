"""Normalise a one-off review export into Field Intelligence's POC snapshot.

The importer deliberately drops reviewer names, profile IDs, profile photos and
review images. The product needs issue evidence and trend timing, not a shadow
profile of customers. The resulting snapshot remains public-web context and can
never create a compliance finding by itself.

Expected input is the clean JSON output of the pinned open-source collector
documented in ADR-010 (YasogaN/google-maps-review-scraper, commit a922af8).
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def _timestamp(value: str | int | None) -> str | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()


def _clean_text(value: str | None) -> str:
    text = (value or "").replace("<br>", "\n").replace("<br/>", "\n")
    # The upstream public payload occasionally replaces punctuation with U+FFFD.
    # Recover only unambiguous contexts; do not invent words.
    text = re.sub(r"(?<=[A-Za-z])\ufffd(?=[A-Za-z])", "'", text)
    text = re.sub(r"(?<=\d)\ufffd(?=\s|$)", "°", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalise(rows: list[dict], *, location_id: str) -> dict:
    reviews: list[dict] = []
    for row in rows:
        raw_id = str(row.get("review_id") or "")
        review = row.get("review") or {}
        text = _clean_text(review.get("text"))
        rating = int(review.get("rating") or 0)
        if rating not in {1, 2, 3, 4, 5}:
            continue
        reviews.append({
            "id": "scr_" + hashlib.sha256(raw_id.encode()).hexdigest()[:20],
            "rating": rating,
            "text": text[:5000],
            "published_at": _timestamp((row.get("time") or {}).get("published")),
            "owner_response_present": bool((row.get("response") or {}).get("text")),
            "provenance": "SCRAPED_PUBLIC_WEB",
        })

    histogram = Counter(str(r["rating"]) for r in reviews)
    return {
        "schema_version": 1,
        "location_id": location_id,
        "source": "SCRAPED_PUBLIC_WEB",
        "collector": {
            "name": "YasogaN/google-maps-review-scraper",
            "commit": "a922af80538afb25c339ab603e256f15db429116",
            "sort": "newest",
        },
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "policy": ("One-off assessment snapshot. Public customer context only; "
                   "never evidence of a compliance violation. Reviewer identity "
                   "and profile data intentionally removed."),
        "summary": {
            "total": len(reviews),
            "written": sum(bool(r["text"]) for r in reviews),
            "rating_only": sum(not r["text"] for r in reviews),
            "rating_histogram": {star: histogram.get(star, 0)
                                 for star in ("1", "2", "3", "4", "5")},
        },
        "reviews": reviews,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--location", default="wolf-creek-atlanta")
    args = parser.parse_args()
    payload = normalise(json.loads(args.input.read_text(encoding="utf-8")),
                        location_id=args.location)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(json.dumps(payload["summary"]))


if __name__ == "__main__":
    main()
