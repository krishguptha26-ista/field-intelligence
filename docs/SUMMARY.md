# Field Intelligence — Krishna Guptha Yanduri

## Approach

I treated this as a trust system, not a checklist demo. In a franchise network,
the expensive error is a confident finding the evidence does not support: it
wastes the consultant's time and damages the franchisor-franchisee relationship.
The product therefore follows one rule: **the agent proposes; a human decides**.

The primary in-the-moment user is the field consultant. The mobile-first Field
Companion guides them area by area while they speak, type, take a photo, record a
short video, or answer structured checks. Voice is transcribed but cannot enter
analysis until the consultant confirms it; photo/video models can describe
observable facts but their schemas cannot return a violation. The agent then
investigates through tenant-scoped, read-only tools and makes a schema-validated
decision. Ambiguous input such as “the restroom floor looked a little dirty”
becomes a targeted question, never a finding. Specific input can become a
candidate finding containing the consultant's exact statement, the model's
separate interpretation, cited standard, severity, confidence, uncertainty,
what the evidence does not establish, and a proposed owner/deadline.

Four gates protect the reviewer: deterministic ambiguity policy, proof that the
cited standard was retrieved in that run, a check that customer sentiment is not
presented as field evidence, and three independent challenge lenses (evidence
sufficiency, franchisee advocate, standards fit). The panel is reviewer-triggered
in the SQLite POC so a consultant is not blocked for ~40 seconds in the field;
missing challenger responses fail closed. Only an independent human approval
creates a corrective action. Closure requires a real after-photo before manager
verification; review, edits, disputes and verification are append-only events.

The implementation is a modular monolith: FastAPI, SQLAlchemy, React/Vite and one
model gateway supporting Gemini or a labelled deterministic fixture engine.
SQLite keeps the POC portable; `DATABASE_URL` switches the same domain model to
Postgres. Prompts are versioned files and every model call records provider,
model, tokens, latency, estimated cost, retries and success. A second EV/depot
tenant runs through the same engine with separate standards, demonstrating the
multi-tenant boundary without claiming production authentication.

## Results

Google Places worked but returned five Google-selected reviews, all positive in
the observed response. I kept that source visible and correctly labelled, then
used a pinned open-source collector once, outside the application request path,
to obtain the complete Wolf Creek public snapshot. The import removes reviewer
names, profiles and photos and hashes review IDs. The resulting 362 rows include
42 one-star, 17 two-star, 28 three-star, 84 four-star and 191 five-star reviews.
The product filters locally to ≤92 days, ≤3 stars and written feedback: 22 recent
ratings become 7 actionable written reviews. Four recurring themes emerge:
hydration availability, service/value response, cart/GPS reliability, and
temporary-green disclosure. Reviews remain context and can never create a
compliance finding.

I extended that signal into an operational loop: recurring themes create
idempotent, assigned triage tickets; staff must validate the issue on site,
attach before and after images, submit a resolution, and obtain independent
manager verification. Only then does the app draft a public owner reply. It does
not pretend Google exposes private reviewer contact details. Rating impact stays
`BASELINE_ONLY` until a later comparable snapshot exists, so the product cannot
manufacture an ROI claim.

“Continuous learning” is also governed. Repeated customer language can propose a
new measurable parameter with anonymized examples, but a named standards owner
must approve or reject it. Approval queues design work; it does not silently
retrain a model or rewrite a standard.

For competitive intelligence, I collected and privacy-minimized 1,235 additional
reviews from three nearby Atlanta public courses. Only aggregate counts, rates
and hashed evidence references ship. Positive-theme rates show Wolf Creek's
relative strengths in course condition, staff hospitality and layout/challenge,
and supported opportunities in practice-facility visibility and value messaging.
The UI labels the manually selected cohort directional, not representative market
research.

Validation is deliberately harder than a happy-path demo. Fourteen API/domain
regression tests pass, including forged provenance, arbitrary standard codes,
unconfirmed voice, high-privacy media, self-review and evidence-free closure.
The deterministic behavioural suite ran each executable
case three times: 14/14 passed, zero flaky, with model-only cases explicitly
skipped rather than counted green; the zero-unsupported-finding release gate
cleared over a non-empty finding set. Separately, live Gemini passed the text
injection case once and the vision injection case three consecutive times. The
production bundle type-checks, `npm audit` reports zero vulnerabilities, the
Docker image builds, and a no-secret production container passed health, review,
benchmark and destructive-reset protection smoke checks. Desktop and 390px mobile
browser validation found no console errors or horizontal overflow. Live Gemini
also transcribed a real WAV, described a real MP4 with time-coded facts, refused
to store that people-filled clip in a high-privacy restroom, and downgraded a
candidate finding after two of three independent challenge lenses weakened it.

## What is honest, and what comes next

BroadPeak supplied no controlled internal standards, credentials or private data.
Wolf Creek uses a sourced, jurisdiction-labelled external guide (law, conditional
requirements, industry BMP and venue policy remain distinct); representative
operating prompts and the EV tenant remain labelled. There is no authentication; the role
selector says “demo persona.” Google Business Profile publishing is a draft-only
integration boundary. Live scraping is not a production dependency. Image files
are content-validated locally, but production still needs identity, object
storage, malware scanning, retention/deletion policy and legal review.

First 30 days: load one redacted standards set, add SSO/RBAC and authorized
Business Profile access, and agree the photo/privacy policy. Days 30–60: run one
property in shadow mode and measure audit time, clarification rate, unsupported
findings, successful disputes and challenge-panel value. Days 60–90: if those
metrics earn trust, enable one live property, compare the post-resolution review
snapshot without claiming causality, and test a second real tenant. The north-star
metric is not findings produced; it is findings a franchisee can successfully
dispute.
