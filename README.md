# Field Intelligence

An evidence-governed AI agent for franchise and venue field audits. Built for the
BroadPeak AI Engineer technical assessment, with Wolf Creek Golf Club (Atlanta)
as the reference location.

Submission artifact: [`output/pdf/Field_Intelligence_Assessment_Summary.pdf`](output/pdf/Field_Intelligence_Assessment_Summary.pdf)
(two pages, as required). The editable source is [`docs/SUMMARY.md`](docs/SUMMARY.md);
deeper decisions and AI-tool usage remain in `docs/adr/` and
`docs/ai-development-log.md`.

**One sentence:** raw audit input goes in; the agent investigates with read-only
tools, argues against its own conclusions, and produces clarifying questions or
evidence-gated findings that only a human can approve — while public customer
signals sit beside the audit as context that can never masquerade as proof.

## 90-second quick start (no keys needed)

```bash
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
uvicorn server.app:app --port 8000
# open http://127.0.0.1:8000
```

Without keys the app still runs end-to-end: a labelled deterministic policy
engine stands in for Gemini, while Wolf Creek customer intelligence uses a
timestamped, anonymized 362-review assessment snapshot. No BroadPeak credential
or asset is required. Every element on screen declares its provenance (see the
Portfolio pulse "What's live vs simulated" panel).

## Live mode

```bash
cp .env.example .env      # add GEMINI_API_KEY and GOOGLE_MAPS_API_KEY
pip install google-genai
```

- `GEMINI_API_KEY` switches analysis to `gemini-2.5-flash` with schema-enforced
  structured output (validation + one retry on schema violation).
- `GOOGLE_MAPS_API_KEY` enables the diagnostic Places API (New) source: the
  location is resolved once to a stable Place ID and fields use explicit masks.
  Places only returns a small Google-selected sample, so the assessment snapshot
  remains the coverage-fit source for Wolf Creek analysis.
- Provider failure degrades to the cached/fixture twin — the demo cannot break.

## The demo in five moments

1. **Restraint** — enter *"The restroom looked a little dirty"* in Live audit.
   The agent asks a targeted clarifying question instead of writing up a
   franchisee. Answer it (one click) and watch the input become a finding only
   when the evidence supports one.
2. **Evidence-gated findings** — every finding shows the consultant's words,
   the model's interpretation (separate), the cited standard, confidence,
   uncertainty, and what the evidence does **not** support.
3. **Human governance** — approve / edit / reject / dispute in the workbench.
   Approval is the only path to a corrective action. Every decision is an
   append-only audit-log entry.
4. **Customer signals with honesty** — all 362 collected ratings are counted,
   then filtered locally to recent (≤92 days), low-rating (≤3★), written reviews.
   Recurring themes use "consistent with, but does not prove" language. One
   mention is an anecdote, never a theme.
5. **Closed-loop resolution** — recurring themes create idempotent, assigned
   triage tickets. A staff member must validate the issue with a before image,
   submit an after image, and a manager independently verifies closure before a
   public owner reply can be drafted.
6. **Competitive edge** — 1,235 anonymized reviews across three nearby Atlanta
   public courses produce aggregate positive-theme rates, relative strengths,
   and supported operating experiments. The cohort and limits are visible.
7. **The platform, not the assignment** — switch the tenant picker to the
   Al Quoz EV & Delivery Depot (a labelled fixture) and run the identical
   pipeline against EV-specific standards. Multi-tenancy shown, not claimed.
   Also see the Digital Truth monitor: a real, cited conflict between Wolf
   Creek's own web channels (yardage and green surface), verified 2026-08-13
   and framed as an opportunity for the Director of Golf — not a violation.

### Field issue lifecycle

Free-form reports and the area guide converge on one record instead of creating
parallel, contradictory work. Clarification uses the complete ordered answer
history, allows only one open question per observation, and stops after two text
turns. A reported issue then requires an explicitly linked photo. The photo is
stored as evidence but labelled `PHOTO_ATTACHED_PENDING_REVIEW`; attachment is
not treated as proof. Once a grounded candidate exists, the matching guide item
is reconciled as reviewer-required and an idempotent ticket is created with an
ID, demo assignee, due date and `PENDING_VALIDATION` status. If no controlled
standard fits, the report is preserved and routed as an operational concern
without inventing a compliance finding.

## How the agent actually works

Analysis runs in two phases with different capabilities (ADR-009):

1. **Investigate** — tools on, no output schema. The agent calls read-only tools
   (`search_standards`, `location_history`, `zone_context`,
   `customer_signal_context`) up to a step budget. Every call is logged and
   persisted as a trace you can open in the UI.
2. **Decide** — tools off, schema enforced. It sees its own transcript and
   returns a validated `AnalysisResult`.

During independent review, **three challengers can attack each candidate
finding on demand** (ADR-011) — evidence sufficiency, franchisee advocate and
standards fit. Deferring them keeps the field capture loop responsive; they run
concurrently on Postgres and sequentially on the SQLite POC to avoid cost-ledger
write locks. Two overturn votes turn the candidate into a clarifying question.
Adjudication is a deterministic vote count, not a fourth model; unavailable
lenses fail closed.

Four gates stand between the model and a reviewer:

| Gate | What it stops |
|---|---|
| `_policy_check` | vague wording promoted to a finding |
| `_grounding_check` | citing a standard the agent never retrieved |
| `_signal_language_check` | public sentiment implied as proof |
| challenge panel | findings that don't survive informed argument |

## Architecture

Modular monolith: FastAPI + SQLAlchemy (SQLite for the POC, `DATABASE_URL`
switches to Postgres) + a React/Vite front end served statically. One model
gateway interface with two providers (Gemini, fixture). One narrow, typed tool
surface — **every tool is read-only**; the LLM never gets a browser, a shell, or
the ability to mutate state. All mutation happens in deterministic Python after a
schema-validated decision returns.

```
server/
  app.py                   # API + static hosting
  config.py                # env config; logs key existence, never values
  models.py                # multi-tenant relational model + append-only audit log
  schemas.py               # typed contracts for every structured LLM output
  gateway.py               # Gemini + fixture providers, tool loop, vision, cost ledger
  agent/orchestrator.py    # investigate → decide → challenge → policy → human
  agent/tools.py           # the read-only tool registry (tenant scope is injected)
  agent/challenge.py       # adversarial panel + deterministic adjudication
  connectors/sources.py    # parallel multi-source fan-out, ranked by trust
  connectors/places.py     # Google Places (New), field masks, fixture twin
  connectors/review_snapshot.py # anonymized, locally filtered assessment snapshot
  connectors/benchmark.py # aggregate competitor strengths and opportunity gaps
  connectors/osm.py        # OpenStreetMap — keyless entity resolution
  connectors/scraper.py    # public-web collection — quarantined, OFF by default
  evals/runner.py          # 16 behavioural cases, N repeats, LLM judge, release gate
prompts/                   # first-class, versioned prompt files (a deliverable)
data/fixtures/             # labelled fixtures incl. verified digital-truth card
docs/adr/                  # architecture decision records
docs/SUMMARY.md            # the two-page written summary
docs/ai-development-log.md
```

## Signal sources (ADR-010)

Queried **in parallel**, ranked by how much we can trust the data is what it
claims to be. Rank never turns sentiment into proof — the highest-trust review is
still context.

| Rank | Source | Reviews? | Notes |
|---|---|---|---|
| 4 | operator's own export | yes | the production path; BroadPeak owns these listings |
| 3 | Google Places (New) | yes | max ~5 Google-selected; not representative |
| 2 | OpenStreetMap | **no** | keyless, free, independent entity resolution |
| 1 | assessment snapshot | yes | one-off, anonymized, coverage-fit; context only |
| 1 | live public-web collection | yes | OFF by default, cache-first, never request-path |

A failing source degrades the result and is shown as such; it never breaks the
request. For this POC the complete snapshots were collected once outside the
request path with a pinned open-source collector, then privacy-minimized. The
production path is an operator-owned export or authorized Business Profile
access, not indefinite scraping.

## Deploy

```bash
docker build -t fieldintel . && docker run -p 8000:8000 fieldintel
```

`render.yaml` is a Render blueprint. Every secret is `sync: false` — set once in
the host's secret manager, never in the repo. **The app runs with no secrets at
all**, falling back to the labelled fixture engine, so a secrets-free deploy is a
legitimate deploy rather than a broken one.

## Evals

```bash
uvicorn server.app:app --port 8000 &     # in one shell
python -m server.evals.runner            # in another (3 repeats)
python -m server.evals.runner --repeats 1  # fast smoke run
```

16 behavioural cases run against the live pipeline and render in the in-app
**Eval Lab**. Two things make this different from a test suite:

- **Every case runs N times and reports a pass *rate*.** A single run of a
  non-deterministic system tells you almost nothing. Anything not unanimous is
  flagged FLAKY — flakiness is a finding about the product, not a reason to
  re-run until green.
- **Semantic assertions are graded by an LLM judge that fails closed.** The
  prompt-injection case is why: the *correct* behaviour (quoting a malicious sign
  as evidence) and the *incorrect* one (obeying it) contain the same words, so a
  substring assertion fails the product exactly when it behaves well. Everything
  mechanically checkable stayed deterministic Python.

The release gate is the **unsupported-finding rate** — a finding that reaches a
reviewer without evidence, without a cited standard, or citing one the agent
never retrieved. The gate is zero.

Cases include the assessment's own traps plus the new attack surface: the
ambiguous "floor looked a little dirty" note, the five-review sample limit,
single-review "themes", prompt injection in text **and in a photograph**,
citation grounding, provenance purity (no fixture leaking into a live sample),
recurrence detection, and re-analysis idempotency.

## Design positions (details in docs/adr/)

- Reviews are context, never proof: a finding requires location evidence plus
  an applicable standard; public sentiment can corroborate or raise questions
  only. The UI keeps signals physically separate from findings.
- The model proposes; humans decide. A deterministic policy layer re-checks
  every model decision before anything reaches a reviewer.
- Cost is a product surface: every model call is a ledger entry (tokens,
  latency, estimated cost, retries) with prices in config, not code.
- Untrusted content is data: observation text, reviews and web content cannot
  select tools, change prompts, or alter behaviour — and there's an eval for it.

## Known limitations (POC honesty)

- Wolf Creek's guide separates sourced federal/Georgia/Fulton requirements,
  conditional requirements, Georgia golf-industry BMPs, venue-published policy,
  and representative operating prompts. Every sourced check links to its basis
  and applicability caveat. BroadPeak supplied no controlled internal standard
  pack or credentials, so field responses remain evidence for human review—not
  legal determinations. The layer is configurable by tenant.
- The per-visit model-call budget is visible and recoverable: a consultant may
  approve up to two small extensions, each retained in the audit trail. Evidence
  is saved before analysis, so the cost pause never discards a capture.
- The fixture LLM engine is deliberately conservative keyword policy, not
  intelligence — it exists so the demo and evals run keyless and deterministic.
- No auth (the role selector is explicitly a demo persona) and single-process
  SQLite. Live Gemini is required for photo, audio and video understanding;
  fixture mode refuses to fabricate media interpretations. Operational
  before/after image uploads are real files with content validation. Production
  still needs SSO/RBAC, object storage, malware scanning and retention policy.
- See `docs/adr/` for what changes on the way to production.
