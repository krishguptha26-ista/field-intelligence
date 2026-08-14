# ADR-009: Two-phase agent — investigate, then decide
Status: accepted · supersedes part of ADR-005's single-call analysis

## Context

The first version made one model call: the whole standards corpus went into the
prompt, and a schema-validated `AnalysisResult` came back. It worked, and it had
two problems that only get worse with scale.

The corpus does not fit. Twelve demo standards do; a real brand manual is
hundreds of clauses across dozens of tenants, and "put it all in the prompt" is
not a plan that survives the first real customer.

More seriously, **a cited standard was indistinguishable from a remembered one**.
`CLN-01` is a plausible-looking code. A model that has seen a few brand manuals
can produce one that does not exist for this tenant, attached to confident and
otherwise well-reasoned prose, and nothing downstream could tell the difference.

## Decision

Analysis runs in two phases with different capabilities.

**Phase 1 — investigate.** Tools on, no output schema. The agent calls read-only
tools (`search_standards`, `location_history`, `zone_context`,
`customer_signal_context`) up to a step budget of 6. Every call is dispatched
through one function, logged to the cost ledger, and persisted as a trace.

**Phase 2 — decide.** Tools off, schema enforced. The agent sees its own
investigation transcript and returns `AnalysisResult`.

Three properties follow:

1. **Citations are checkable.** `_grounding_check` rejects any finding citing a
   standard that was not returned by a `search_standards` call *in that run*. The
   finding is demoted to a clarifying question. The model is free to be wrong; it
   is not free to be unverifiable.
2. **Gathering ends before deciding begins.** An agent that can still call tools
   while writing its verdict can keep hunting for support for a conclusion it has
   already reached. Separating the phases removes that move.
3. **The reasoning is an artifact.** "Every finding is reconstructable" stopped
   being a claim about the architecture and became a row in the database and a
   drawer in the UI.

Automatic function calling is disabled even though the SDK offers it. It would
run the loop for us, and the tool calls would then be unbudgeted, unlogged, and
unreconstructable — which is the one thing this product cannot trade away.

## Consequences

- More calls per audit (one per tool round plus the decide call, plus three for
  the challenge panel in ADR-011). Measured cost stayed in fractions of a cent;
  the cost console shows the real number rather than an estimate.
- If the agent proposes no retrieval at all, every citation would fail grounding
  and the audit would collapse into clarifications. A deterministic fallback
  retrieval runs in that case, labelled `SYSTEM_FALLBACK` in the trace so nobody
  reads it as the agent's own initiative.
- Retrieval is lexical (token overlap + category match), not embeddings. It is
  deterministic, so the same note retrieves the same clauses every run and evals
  stay reproducible. Embeddings are the production swap when the corpus grows;
  the interface does not change.

## Rejected

- **Keep one call, validate the code afterwards.** Catches invented codes, but
  not a real code the model never looked at — and the difference between those
  two is exactly the discipline being enforced.
- **Let the model call tools during the decide phase.** Fewer round trips, but it
  reintroduces motivated retrieval.
- **An agent framework.** ADR-005 still holds. The orchestrator owns control
  flow; the tools are a typed registry; nothing here needs a graph library.
