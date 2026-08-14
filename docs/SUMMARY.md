# Field Intelligence — Krishna Guptha Yanduri

## Why I built it this way

At Goldman I own a control that sits between the trading day and the books. It
reconciles P&L, flags the numbers that don't tie out, explains each exception in
language a controller can act on, and then stops. A human clears it. Only then do
the numbers lock. The platform's value isn't that it finds breaks — anyone can
write a diff. It's that it's trusted enough to be believed, and disciplined
enough that nobody is ever accused of a break that isn't one. That took the
exception cycle from about a day to six minutes, and match rates from roughly
40% to 98–99%.

A field audit is the same problem wearing different clothes. A consultant walks a
property, sees things, writes them up. The write-up varies by who held the
clipboard. Evidence and opinion blur together. And the worst outcome isn't a
missed issue — it's a franchisee accused of something the evidence didn't
support. That's a relationship, not a ticket.

So I built the same control: **detect, explain, human clears, lock.** The system
proposes; it never decides.

## What it does

Raw audit input goes in — a typed note, a checklist item, or a photograph. What
comes out is either a clarifying question or a candidate finding with its
evidence attached, its cited standard, its confidence, and an explicit list of
what the evidence does *not* establish. A human approves, edits, rejects or
disputes. Only approval creates a corrective action. Every step is an
append-only log entry.

The part I'd point at first is what it refuses to do. Type *"the restroom looked
a little dirty"* and it asks which restroom and what exactly you saw, rather than
writing up a franchisee. That behaviour is enforced in four independent places,
because a prompt alone will not hold it.

Four things it does that a checklist tool doesn't:

**It investigates before it decides.** The agent doesn't get the standards handed
to it. It calls read-only tools to retrieve them, look up the location's history,
and check the zone — then decides in a second pass with the tools switched off. A
finding may only cite a standard the agent actually retrieved in that run; if it
cites one it didn't, the finding is automatically demoted to a question. `CLN-01`
is a plausible-looking code, and a model that has read a few brand manuals can
invent one. This makes a cited standard different from a remembered one.

**It remembers.** The restroom finding in the demo comes back as CRITICAL, not
HIGH, with the reason attached: this exact issue was found here 118 days ago,
corrected, and signed off — and it's back. A first occurrence is an incident. A
recurrence after sign-off is a process failure. Those are different conversations
to have with an operator, and no checklist app can tell them apart.

**It argues with itself.** Before any finding reaches a human, three challengers
attack it in parallel: one on whether the evidence supports the claim, one
representing the franchisee making the strongest honest case that the finding is
unfair, and one checking whether the cited standard actually covers what was
seen. Two overturn votes and the finding never reaches the reviewer — it becomes
a question instead. The reviewer sees the arguments and can disagree with the
panel as easily as with the model. This is the opposite of automating the human
away. It hands them the case for the defence.

**It reads photographs without judging them.** The vision model describes what's
in the frame and lists what the image doesn't establish. It has no way to cite a
standard or reach a verdict — its output schema has no field for one. The
description becomes an observation and goes through the same pipeline as
anything typed. One-step "photo in, violation out" is the demo that wins a
bake-off and loses a franchisee.

## What it cost and what it caught

Measured on the live system, not estimated: **141 model calls across 28 audits,
$0.144 total — $0.0051 per completed audit**, about half a US cent, at ~7s
median latency. Adding the challenge panel roughly tripled the per-audit cost.
Against a consultant hour, that is not a number worth optimising yet, and the
console shows the real figure rather than a projection.

The evaluation suite is 16 behavioural cases, each run multiple times, reporting
a pass *rate* rather than a pass. The release gate is the unsupported-finding
rate: a finding that reaches a reviewer without evidence, without a cited
standard, or citing one the agent never retrieved. The gate is zero and it is
currently clear.

Two bugs the discipline caught that I'd have shipped otherwise. Once the live
Google data came on, real reviews began blending with seeded demo ones under a
single `LIVE_API` label — the one failure that would justify distrusting every
other label on the screen. And the challenge panel voted 3–0 to overturn a
perfectly good finding, which turned out to be my bug: I was showing the
challengers the original vague note and not the clarification that answered it.
A panel that rubber-stamped would have hidden that.

I also rewrote the eval suite because it was lying to me. The old version scored
9/10 with a different case failing each run — noise presented as a number. Worse,
its prompt-injection assertion was a substring match, and the *correct* behaviour
(quoting a malicious sign as evidence) contains the same words as the *incorrect*
one (obeying it). It was failing the product precisely when the product behaved
well. Semantic assertions are now graded by a judge that fails closed; everything
mechanically checkable stayed deterministic.

## How it's built

A modular monolith: FastAPI and SQLAlchemy behind a React front end, SQLite for
the POC with Postgres one environment variable away. One gateway interface with
two providers, so swapping models is configuration and the eval suite is the net
that makes swapping safe. No agent framework — one orchestrator owns control
flow, the tools are a narrow typed registry, and every one of them is read-only.
The model never gets a browser, a shell, or the ability to change state. All
mutation happens in deterministic Python after a schema-validated decision comes
back.

Public signals are queried from four sources in parallel and ranked by trust,
because during this build the Google Places API was switched off at the project
level and there was nothing in the code that could fix it. An evidence layer that
someone else's console can disable isn't an evidence layer. OpenStreetMap now
answers the question that matters most — *is this the right place?* — with no key
and no quota, and it independently confirmed the location and turned up a
cross-channel name discrepancy for free.

Reviews are never proof. They sit visually apart from findings, carry an n≤5
sample warning, can only ever say "consistent with, but does not prove", and
cannot create a finding under any circumstances. When the agent's own wording
drifted toward implying otherwise, I added a deterministic check that catches it
and labels the correction, because on this particular rule a prompt is not enough.

## What's honest about it

The standards are representative, not BroadPeak's. I've asked for a real
redacted checklist; the layer is per-tenant configurable and swapping them is
data entry, not engineering. The second tenant — an EV and delivery depot in Al
Quoz — is a labelled fixture, but it runs the identical pipeline against its own
standards, which is the actual claim. Verification photos are simulated and
labelled as such. There's no auth, just a role switcher. The public-web scraper
exists but is off by default, and I've documented plainly that on this location
it returns nothing and will break when the page changes.

Every screen declares whether what you're looking at is live, cached, fixture or
simulated. That panel makes the demo look less impressive and makes it more
believable, which is the right trade.

## Where I'd take it

**First 30 days:** ground truth. Real standards, read-only access to the Business
Profile data BroadPeak already owns, and a camera and privacy policy agreed
before a single frame is captured. **Days 30–60:** shadow mode at one property —
the agent runs alongside a consultant and changes nothing, while I measure
write-up time, unsupported-finding rate, and how often the challenge panel was
right. **Days 60–90:** if those numbers earn it, one property in live use and the
second tenant type stood up to prove the engine travels.

Three things I'd want from you: one redacted real audit checklist, read-only
access to owned reviews for one location, and one pilot property for sixty days.

The thing I'd measure is not how many findings it produces. It's how many
findings a franchisee successfully disputes. At Goldman the number that mattered
was never breaks found — it was breaks wrongly raised. Same control, different
building.
