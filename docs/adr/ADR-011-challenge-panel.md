# ADR-011: An adversarial challenge panel before human review
Status: accepted

## Context

The product's stated personality is that it is hard to convince. Until this
change, that was enforced by a prompt telling the model to be careful and a
deterministic policy layer checking its output. Both sit on the *same side of
the argument* as the model that produced the finding. Nothing in the system was
trying to knock a finding down.

The failure mode that matters here is not the missed issue. It is the wrong
accusation: a plausible, well-written, confidently-cited finding that a
franchisee can successfully dispute. That costs a relationship, and it costs the
reviewer's trust in every finding that follows.

## Decision

Every candidate finding is attacked by three independent challengers before a
human sees it.

- **evidence_sufficiency** — is there an inferential leap between what was
  observed and what is claimed? A moment described as an ongoing condition, one
  instance generalised to a process, cause asserted from a symptom.
- **franchisee_advocate** — the strongest honest case that this finding is
  unfair or premature. Outside their control, already being handled, normal
  mid-service state, a snapshot rather than a standard of operation.
- **standards_fit** — ignoring whether something is wrong, is the *cited*
  standard the right one and does the observation fall inside its scope? A real
  condition cited against a near-miss standard is still a defective finding, and
  it is the kind that gets successfully disputed.

They run concurrently and **never see each other's arguments**. Challengers that
can read each other converge, and three converged opinions are one opinion
wearing a panel's clothing.

**Adjudication is deterministic Python, not a fourth model.**

| Votes | Outcome |
|---|---|
| 2+ OVERTURN | Never reaches a human. Becomes a clarifying question built from the challengers' own "what would settle it" answers |
| 1 OVERTURN, or 2+ WEAKEN | Survives, downgraded: severity −1, confidence −0.15, every objection recorded on the finding |
| otherwise | Upheld, with the challenge record attached |

Asking an LLM to referee LLMs adds a failure mode and removes an auditable one.
A vote count is something a reviewer checks in four seconds.

A challenger that errors is recorded as `ABSTAIN` and counts toward nothing — a
failed challenger must never silently become an UPHOLD.

## Consequences

- Three extra calls per candidate finding. On measured POC numbers that is a
  fraction of a cent, against the cost of one consultant-hour spent defending a
  finding that should not have been written. `ENABLE_CHALLENGE_PANEL=false`
  turns it off for latency-sensitive demos and for measuring what it changes.
- The panel is visible in the UI. A reviewer sees the actual argument made
  against the finding, in the challenger's own words, and can disagree with the
  panel as easily as with the model. This is the opposite of automating the
  human away: it hands them the case for the defence.
- It caught a real wiring bug during development. The panel voted 3–0 to
  overturn a valid finding because it was being shown only the original vague
  observation and not the clarification answer that supplied the specifics. The
  challengers were right about what they were shown; the orchestrator was
  showing them the wrong thing. A panel that had rubber-stamped it would have
  hidden the bug.
- ADR-005 still holds. This is a bounded fan-out owned by the orchestrator, not
  autonomous agents negotiating with each other. Control flow stays deterministic
  and the human stays the only path to approval.

## Rejected

- **A single "critic" call.** One critic with one brief finds one class of
  problem, and shares the original model's blind spots.
- **An LLM judge to adjudicate.** Unauditable, and it makes the most consequential
  step in the chain the least inspectable one.
- **Running the panel after human review.** Pointless. The value is spending the
  reviewer's attention only on findings that survive informed argument.
