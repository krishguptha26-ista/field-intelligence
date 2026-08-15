"""Read and filter a privacy-minimised, one-off public review snapshot.

Collection is deliberately outside the request path. The running application
never scrapes Google; it reads a timestamped assessment artifact, calculates the
recency window locally, and labels every row SCRAPED_PUBLIC_WEB.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

from .. import config


SNAPSHOTS = {
    "wolf-creek-atlanta": config.FIXTURES_DIR / "wolf_creek_reviews_snapshot.json",
}


def load_review_snapshot(location_id: str, *, window_days: int = 92,
                         max_rating: int = 3) -> dict | None:
    path = SNAPSHOTS.get(location_id)
    if path is None or not path.exists():
        return None
    blob = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    eligible: list[dict] = []
    recent_count = low_rating_count = rating_only_count = 0
    seen: set[str] = set()

    for raw in blob.get("reviews", []):
        review_id = str(raw.get("id") or "")
        if not review_id or review_id in seen:
            continue
        seen.add(review_id)
        try:
            published = datetime.fromisoformat(str(raw["published_at"]).replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            days_ago = (now - published).days
            rating = int(raw["rating"])
        except (KeyError, TypeError, ValueError):
            continue
        if days_ago < 0 or days_ago > window_days:
            continue
        recent_count += 1
        if rating > max_rating:
            continue
        low_rating_count += 1
        text = (raw.get("text") or "").strip()
        if not text:
            rating_only_count += 1
            continue
        eligible.append({
            "id": review_id,
            "rating": rating,
            "text": text,
            "author": "Anonymous public reviewer",
            "published_at": published.isoformat(),
            "days_ago": days_ago,
            "provenance": "SCRAPED_PUBLIC_WEB",
            "owner_response_present": bool(raw.get("owner_response_present")),
        })

    eligible.sort(key=lambda r: (r["days_ago"], r["rating"]))
    return {
        "reviews": eligible,
        "provenance": "SCRAPED_PUBLIC_WEB",
        "window_days": window_days,
        "selection": f"published within {window_days} days; rating <= {max_rating}; written reviews",
        "sample_caveat": (
            "One-off public-web snapshot collected for this assessment; reviewer identity removed. "
            "Recent low-rating feedback is customer context, not compliance evidence."),
        "captured_at": blob.get("captured_at"),
        "collector": blob.get("collector"),
        "dataset_summary": {
            **(blob.get("summary") or {}),
            "recent_all_ratings": recent_count,
            "recent_low_rating": low_rating_count,
            "recent_low_rating_written": len(eligible),
            "recent_low_rating_without_text": rating_only_count,
        },
        "location_meta": {},
    }
