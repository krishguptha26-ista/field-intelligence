# ADR-010: Plural signal sources, ranked by trust; collection outside request paths

Status: accepted

## Context

Google Places (New) returns only a small Google-selected review sample. During
the build it also failed when the API was disabled or the key restricted the
wrong Places product. Neither problem can be repaired from application code.
BroadPeak supplied no Business Profile credentials, review export, standards or
other private asset for this assessment.

OpenStreetMap solves independent place resolution, but it has no reviews. A
full owned-location review feed is available in production through an authorized
Google Business Profile integration or an operator export, neither of which is
available to this POC.

## Decision

Sources are plural, queried concurrently, and ranked.

| Trust class | Source | Use |
|---|---|---|
| `OPERATOR_OWNED` (4) | operator export / authorized Business Profile data | preferred production review source |
| `OFFICIAL_API` (3) | Google Places (New) | listing facts and a small diagnostic review sample |
| `OPEN_DATA` (2) | OpenStreetMap | independent entity resolution; no reviews |
| `SCRAPED_WEB` (1) | timestamped assessment snapshots | POC customer context only |

Two rules hold regardless of source:

- Trust rank never converts sentiment into proof. Reviews may open triage or
  suggest a question; they cannot create a compliance finding.
- A failing source degrades the result and remains visible; it does not break
  the request or silently blend its rows with another provenance.

### Assessment snapshot

The POC uses `YasogaN/google-maps-review-scraper` pinned at commit
`a922af80538afb25c339ab603e256f15db429116` for one-off collection outside the
application request path. The import step removes reviewer names, profile IDs,
profile images and review photos, hashes review IDs, retains only rating, text,
publication time and owner-response presence, and stamps every row
`SCRAPED_PUBLIC_WEB`.

The verified Wolf Creek artifact contains 362 reviews:

| Rating | Count |
|---:|---:|
| 1★ | 42 |
| 2★ | 17 |
| 3★ | 28 |
| 4★ | 84 |
| 5★ | 191 |

The product calculates its ≤92-day, ≤3★, written-review window locally. This is
why the five positive Places reviews no longer hide the low-rating signal.

Three nearby Atlanta public-course snapshots are used for a directional
competitor benchmark: Brown's Mill (481), Alfred Tup Holmes (366), and Chastain
Park (388), or 1,235 comparator rows. Only aggregate positive-theme rates and
hashed evidence references reach the API. The UI explicitly says the manually
selected three-course cohort is not representative market research.

Live browser collection remains quarantined behind configuration, cache-first,
and absent from the normal page request. The snapshot is reproducible through
`scripts/import_reviews_snapshot.py`; it is not silently refreshed.

## Consequences

- Wolf Creek review analytics work with no BroadPeak dependency or secret.
- Source selection is based on ownership first, then coverage fitness; the
  assessment snapshot may be selected over Places while retaining its lower
  trust label.
- Every selected sample has one provenance. The eval suite rejects a mixed or
  mislabeled sample.
- Review-derived tickets require on-site validation and before/after evidence.
- Taxonomy gaps enter a human approval queue; review content never silently
  retrains a model or rewrites a standard.
- A later comparable snapshot can measure directional rating movement, but the
  system reports `BASELINE_ONLY` until that data exists and makes no ROI claim.

## Production transition

Use an operator-owned export or authorized Google Business Profile integration
for owned locations, with consent, retention limits, deletion handling and API
terms reviewed by counsel. Public owner replies require that authorization.
Google does not provide a reviewer's private contact details, so private outreach
cannot be promised; the app drafts a public owner response only after verified
closure.

## Rejected

- Places' five selected reviews as the analytical dataset: too small and
  selection-biased for theme claims.
- Live scraping in a page request: fragile, slow, difficult to govern and a
  third-party terms risk.
- Review sentiment as proof of a violation: customer context is not field
  evidence.
- Automatic hourly retraining or taxonomy mutation: unreviewed drift would make
  standards and behaviour unauditable.
