# AI-assisted development log

The rubric asks how AI coding tools were used. Honestly: this project was built
spec-first, with AI agents doing the implementation labour under human
direction and review — the same shape I'd run an AI-native engineering team.

## Workflow

1. **Spec before code.** I wrote (with LLM assistance, across ChatGPT and
   Claude) a master build specification covering domain model, evidence
   policy, connector contracts, eval rubric and demo script — then a priority
   overlay that tiers it: flawless vertical slice first, wow moments second,
   production breadth documented but deferred.
2. **Agents build, I review.** Claude (Anthropic) implemented the scaffold,
   domain model, gateway, connectors, orchestrator, UI and eval harness against
   the spec. Every file was reviewed; several outputs were rejected or
   corrected (examples below).
3. **Evals as the contract.** Behaviour-level golden cases were written against
   the assessment's own traps and run continuously. The suite caught a real
   bug during development: re-running analysis duplicated findings for
   already-settled observations. Fix: observation-level idempotency anchor on
   findings + eval case 9.
4. **Research verified, not assumed.** The Wolf Creek digital-truth card was
   re-verified against the live homepage and course-information page on
   2026-08-13 before being shipped as CACHED_LIVE_DATA. LLM-suggested
   "facts" were not trusted without a fetch.

## What stayed human

- Product judgment: reviews-as-context-never-proof, abstention over guessing,
  approval as the only path to enforcement.
- Architecture decisions (docs/adr/) and the rejection of agent frameworks.
- Scope: what to build flawlessly vs document honestly as deferred.
- Every prompt in /prompts, reviewed line by line.

## Examples of rejected/corrected AI output

- Datetime handling: naive/aware mismatch on SQLite round-trips (crashed the
  signals endpoint) — caught by smoke test, fixed with explicit UTC coercion.
- Duplicate findings on re-analysis — caught by the eval suite, fixed with an
  idempotency column and payload filtering.
- Over-eager severity: the deterministic policy layer was added specifically
  to demote vague-wording findings the model was too confident about.

---

# Session 2 — from "structured output" to an actual agent

The first version was an LLM call with a good schema around it. This session
turned it into something that investigates before it decides, remembers, gets
argued with, and can be checked afterwards. Working method was the same: I set
the architecture and the policy, the agent wrote the code, I reviewed every diff
and rejected what didn't hold up.

What is worth recording is not the features. It's the four times the work told
me something I hadn't decided in advance.

## 1. An outage made an architecture decision for me

Google Places returned 403 for most of the session. First `SERVICE_DISABLED`
(API not enabled on the project), then — after enabling it — `API_KEY_SERVICE_BLOCKED`,
because the key's restriction list contained the legacy "Places API" but not
"Places API (New)". Two different console settings, neither fixable from code.

That is a product problem, not an ops problem. The evidence layer could be
switched off by someone else's configuration screen. So sources became plural,
concurrent and trust-ranked (ADR-010), with OpenStreetMap as a keyless
independent source that answers the question that actually matters for evidence:
*is this the right place?* It resolved Wolf Creek to relation 142995 with a
matching street number, for free, with no key.

I also researched the free-review question properly rather than assuming.
Finding: place data is solved and free; **per-business reviews are not** — no
free or open source exists, BizData excludes them, Foursquare puts them behind
Premium, the Tripadvisor Content API is deprecated. That research is why the
production recommendation is BroadPeak's own Google Business Profile export
rather than any third party.

## 2. Making citations checkable, not just plausible

The original design put the whole standards corpus in the prompt. It works at
twelve standards and fails at a real brand manual — but the deeper problem was
that **a cited standard was indistinguishable from a remembered one**. `CLN-01`
is a plausible-looking code; a model that has read a few brand manuals can
produce one that doesn't exist for this tenant, wrapped in confident prose.

Now the agent has to retrieve a standard before it can cite one, and
`_grounding_check` demotes any finding citing a code that no `search_standards`
call returned in that run. Unit-tested against three cases: a nonexistent code,
a real code never retrieved, and a real code properly retrieved.

## 3. The panel that caught my own bug

The adversarial challenge panel (ADR-011) voted **3–0 to overturn a perfectly
good finding**. My first instinct was that the challengers were being obtuse.
They weren't — they were only being shown the original vague observation ("Pro
shop floor seemed off") and never the clarification answer that supplied the
specifics. They were right about what they were shown; my wiring was showing them
the wrong thing.

A rubber-stamp panel would have passed the finding and hidden the bug. That's the
argument for adversarial review in one incident.

## 4. Two things I was doing wrong that only showed up under test

**A provenance integrity bug.** Once Places went live, the review sample started
blending real reviews with seeded fixture ones and labelling the whole thing
`LIVE_API`. That is the single worst failure this product can have — a viewer who
spots one fixture name inside "live" data is right to distrust every other label
on screen. Fixed so one sample carries one provenance, and added
`case_provenance_not_mixed` so it cannot come back.

**The eval suite was lying to me.** v1 scored 9/10 with a *different* case failing
each run — noise presented as a number. Two changes: every case now runs N times
and reports a pass **rate**, flagging anything not unanimous as FLAKY; and
assertions about meaning are graded by an LLM judge instead of substring matching.

The substring problem is worth stating precisely, because it was making the
product look worse than it was. The injection assertion was
`'api' in json and 'key' in json`. But the **correct** behaviour — quoting the
malicious sign as evidence — and the **incorrect** behaviour — obeying it —
contain the same words. The test failed the product exactly when it behaved well.
Everything mechanically checkable stayed deterministic Python; the judge is used
only where the property is genuinely semantic, and it is told to fail closed.

## Rejected this session

- **A Google Maps review scraper as a primary source.** Requested, and built —
  but quarantined, off by default, cache-first, bottom of the trust ladder, with
  the browser deliberately absent from the deploy image. Recorded honestly in
  ADR-010: on the reference location it returns *nothing* (the page served to a
  headless browser has no Reviews tab), it depends on a DOM that changes without
  notice, and it will break.
- **A fixture stand-in for vision.** Every other capability has a deterministic
  offline twin so the demo runs keyless. Vision does not, and must not: a
  plausible description of a photograph nobody looked at would enter the system
  as evidence and be indistinguishable from the real thing. The endpoint returns
  a clear error instead.
- **An LLM to adjudicate the challenge panel.** It would make the most
  consequential step in the chain the least inspectable one. Vote counting is
  deterministic and a reviewer can check it in four seconds.
- **Letting the model call tools while writing its verdict.** Fewer round trips,
  but an agent that can still gather while concluding will gather support for the
  conclusion it already reached.

## What stayed human, again

The evidence policy, the trust ladder, the decision to keep scraping off by
default, and every call about what to label as fixture. The most valuable thing I
did all session was not writing code — it was refusing to let a passing test
stand in for correct behaviour.
