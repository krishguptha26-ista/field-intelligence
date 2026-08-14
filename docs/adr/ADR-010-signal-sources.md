# ADR-010: Plural signal sources, ranked by trust; scraping quarantined
Status: accepted

## Context

This one was decided by an outage rather than a whiteboard.

For most of this build the Google Places API returned 403. First
`SERVICE_DISABLED` — the API was not enabled on the project. Then, once enabled,
`API_KEY_SERVICE_BLOCKED` — the key's API restriction list contained the legacy
"Places API" but not "Places API (New)", which are separate entries granting
access to different endpoints.

Both were console settings. Neither was fixable from inside the application, and
during that time the product had no independent way to say anything about the
public record. **A product whose evidence layer can be switched off by someone
else's configuration screen is a demo waiting to fail.**

A survey of the alternatives found an asymmetry worth designing around:

- **Place data is solved and free.** OpenStreetMap (Nominatim for geocoding,
  Overpass for POI queries) is keyless, has no quota, and is independently
  maintained. It resolved Wolf Creek to relation 142995 with a matching street
  number.
- **Reviews are not.** No free or open source of per-business reviews exists.
  BizData (OSM-derived) explicitly excludes them. Foursquare puts tips and
  ratings behind Premium with no free tier. The Tripadvisor Content API is
  deprecated. Google removed the Places free tier in February 2025.

## Decision

**Sources are plural, queried concurrently, and ranked.**

| Trust class | Source | Notes |
|---|---|---|
| `OPERATOR_OWNED` (4) | the operator's own listing export | Highest trust; the production path — BroadPeak owns these listings through Google Business Profile |
| `OFFICIAL_API` (3) | Google Places (New) | Under contract, rate-limited, revocable |
| `OPEN_DATA` (2) | OpenStreetMap | Keyless, free, independent. Entity resolution and place facts. **No reviews exist in OSM** |
| `SCRAPED_WEB` (1) | public web collection | Off by default. Cache-first. Never the sole basis for anything |

Two rules hold regardless of source:

- **Trust rank never converts sentiment into proof.** The ladder ranks how much
  we believe the data is what it claims to be, not what it may be used for. A
  five-star review from the highest-trust source is still context.
- **A failing source degrades the result; it never breaks the request.** The
  fan-out returns partial results with each source's status attached, because
  "three of four sources answered, and here is which one" is information a
  reviewer is entitled to.

**Scraping is built, isolated, and off.** `connectors/scraper.py` is quarantined
in its own module, gated behind `ENABLE_SCRAPED_SIGNALS` (default false),
cache-first so no live demo depends on a collection completing, and stamped
`SCRAPED_PUBLIC_WEB` at the bottom of the ladder. The browser it needs is
deliberately absent from the deployed image.

The reasoning is not squeamishness. Collection that depends on a third party's
terms is a business decision with legal surface, and a product whose entire
thesis is evidence governance has no business quietly making that call on an
operator's behalf. The capability exists so the decision can be made; the default
does not presume it.

Recorded honestly, because it is the more useful finding: on the reference
location the collector returns **nothing**. The place page served to a headless
browser has only "Overview" and "About" tabs — no Reviews tab at all. It is the
most fragile component in the system, it depends on a DOM that changes without
notice, and it will break.

## Consequences

- One `SignalResult` shape covers every source, so adding one is a registry entry.
- The parallel fan-out surfaced a real bug immediately: live and fixture reviews
  were blending into a single sample labelled `LIVE_API`. One sample now carries
  one provenance, and `case_provenance_not_mixed` in the eval suite keeps it that
  way. A viewer who spots one fixture name inside "live" data is right to
  distrust every other label on the screen.
- OSM cross-checks produce genuine findings for free: independent entity
  confirmation, and a cross-channel name variance (the operator trades as "Wolf
  Creek Golf **Club**"; the public map record says "Golf **Course**") that feeds
  the digital-truth card.
- Overpass was tried and dropped from the live path — all three public mirrors
  returned 504 during testing. Nominatim is reliable and answers the question
  that matters.

## Rejected

- **Scraping as the primary review source.** Fragile, ToS-adjacent, needs a
  ~150MB browser that does not fit the deploy target, and it would be the single
  component a technical reviewer distrusts in a product about evidence.
- **Dropping reviews entirely when Places is down.** Reviews are context; losing
  them costs nothing in finding quality — but losing the *place facts* costs
  entity resolution, and OSM fixes that for free.
- **A paid review aggregator.** Solves a POC problem by spending money on the
  wrong layer. The operator already owns this data.
