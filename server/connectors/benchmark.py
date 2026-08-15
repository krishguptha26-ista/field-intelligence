"""Privacy-minimised, directional competitor benchmark from review snapshots.

This is not market research and never creates a compliance finding. It compares
aggregate positive-theme mention rates across a deliberately small, disclosed
cohort of nearby public golf courses. Reviewer identity and verbatim review text
never leave the snapshot layer.
"""
from __future__ import annotations

from collections import Counter
import json
import re
from statistics import median

from .. import config


COHORT = [
    ("wolf-creek-atlanta", "Wolf Creek Golf Club", "wolf_creek_reviews_snapshot.json"),
    ("browns-mill-atlanta", "Brown's Mill Golf Course", "competitor_browns_mill_reviews_snapshot.json"),
    ("alfred-tup-holmes-atlanta", "Alfred Tup Holmes Golf Course", "competitor_alfred_tup_holmes_reviews_snapshot.json"),
    ("chastain-park-atlanta", "Chastain Park Golf Course", "competitor_chastain_park_reviews_snapshot.json"),
]

THEMES = [
    ("staff_hospitality", "Staff hospitality",
     re.compile(r"friendly|helpful|welcom|professional|customer service|staff (?:was|were|is|are) (?:great|awesome|amazing)", re.I),
     "Turn consistently named service behaviours into coaching examples and a pre-shift recognition loop."),
    ("course_condition", "Course and green condition",
     re.compile(r"great (?:shape|condition)|good (?:shape|condition)|well[- ]maintained|greens? (?:were |are |is )?(?:great|good|excellent|amazing|pure)|beautiful course", re.I),
     "Make current playing conditions and completed maintenance improvements visible before booking."),
    ("pace_of_play", "Pace of play",
     re.compile(r"pace (?:was|is) (?:great|good|fast)|quick (?:round|pace)|finished (?:in|under|right)|moved (?:well|quickly)|fast round", re.I),
     "Measure round duration by tee-time band and surface the reliable fast-play windows."),
    ("layout_challenge", "Layout and challenge",
     re.compile(r"great layout|good layout|course layout|challenging|fun (?:course|layout)|variety of holes|interesting holes", re.I),
     "Package the course's distinctive challenge with hole-level guidance for first-time guests."),
    ("value", "Value for money",
     re.compile(r"great value|good value|worth (?:the|it)|reasonable price|affordable|for the price", re.I),
     "Tie price messaging to the concrete experience included, especially where conditions have improved."),
    ("practice_facilities", "Practice facilities",
     re.compile(r"driving range|practice (?:area|facility|facilities|green)|putting green|chipping area", re.I),
     "Promote and operationally verify practice-facility availability as part of the pre-round experience."),
]


AGGREGATE_PATH = config.FIXTURES_DIR / "competitor_benchmark_aggregate.json"


def _load(filename) -> dict:
    path = filename if hasattr(filename, "read_text") else config.FIXTURES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _course_stats(course_id: str, name: str, filename: str) -> dict:
    blob = _load(filename)
    positive = [r for r in blob.get("reviews", [])
                if int(r.get("rating") or 0) >= 4 and (r.get("text") or "").strip()]
    counts: Counter[str] = Counter()
    refs: dict[str, list[str]] = {key: [] for key, *_ in THEMES}
    for review in positive:
        text = review["text"]
        for key, _label, pattern, _recommendation in THEMES:
            if pattern.search(text):
                counts[key] += 1
                if len(refs[key]) < 5:
                    refs[key].append(review["id"])
    denominator = len(positive)
    return {
        "id": course_id, "name": name,
        "total_reviews": (blob.get("summary") or {}).get("total", len(blob.get("reviews", []))),
        "positive_written_reviews": denominator,
        "rating_histogram": (blob.get("summary") or {}).get("rating_histogram", {}),
        "captured_at": blob.get("captured_at"),
        "theme_counts": dict(counts),
        "theme_rates": {key: round(counts[key] * 100 / denominator, 1) if denominator else 0.0
                        for key, *_ in THEMES},
        "evidence_refs": refs,
    }


def build_competitor_benchmark(cohort=COHORT) -> dict:
    """Build the privacy-minimised aggregate from explicitly supplied snapshots."""
    courses = [_course_stats(*course) for course in cohort]
    subject = courses[0]
    peers = courses[1:]
    comparisons = []
    for key, label, _pattern, recommendation in THEMES:
        ours = subject["theme_rates"][key]
        peer_rates = [p["theme_rates"][key] for p in peers]
        leader = max(peers, key=lambda p: p["theme_rates"][key])
        leader_rate = leader["theme_rates"][key]
        peer_median = round(median(peer_rates), 1)
        gap = round(leader_rate - ours, 1)
        leader_support = leader["theme_counts"].get(key, 0)
        classification = ("OPPORTUNITY" if gap >= 2.0 and leader_support >= 3 else
                          "RELATIVE_STRENGTH" if ours >= peer_median and
                          subject["theme_counts"].get(key, 0) >= 3 else "NO_CLEAR_SIGNAL")
        comparisons.append({
            "key": key, "label": label, "subject_rate": ours,
            "subject_mentions": subject["theme_counts"].get(key, 0),
            "peer_median_rate": peer_median,
            "leader": leader["name"], "leader_rate": leader_rate,
            "leader_mentions": leader_support, "gap_to_leader_pp": gap,
            "classification": classification,
            "recommendation": recommendation if classification == "OPPORTUNITY" else "",
            "leader_evidence_refs": leader["evidence_refs"][key],
        })
    comparisons.sort(key=lambda row: (
        0 if row["classification"] == "OPPORTUNITY" else
        1 if row["classification"] == "RELATIVE_STRENGTH" else 2,
        -row["gap_to_leader_pp"], row["label"]))
    return {
        "location_id": "wolf-creek-atlanta",
        "provenance": "SCRAPED_PUBLIC_WEB_AGGREGATE",
        "cohort": [{k: course[k] for k in ("id", "name", "total_reviews",
                                             "positive_written_reviews", "rating_histogram",
                                             "captured_at")} for course in courses],
        "method": ("Directional comparison of theme mentions per 100 written 4-5 star reviews. "
                   "Three manually selected Atlanta public-course comparators; no reviewer identity, "
                   "verbatim review text, causal claim, or claim of market representativeness."),
        "comparisons": comparisons,
        "recommendations": [row for row in comparisons if row["classification"] == "OPPORTUNITY"],
    }


def competitor_benchmark(location_id: str) -> dict | None:
    if location_id != "wolf-creek-atlanta" or not AGGREGATE_PATH.exists():
        return None
    return json.loads(AGGREGATE_PATH.read_text(encoding="utf-8"))
