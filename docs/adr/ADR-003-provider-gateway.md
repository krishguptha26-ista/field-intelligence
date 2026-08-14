# ADR-003: Provider gateway with a deterministic fixture engine
Status: accepted

All LLM access goes through one interface. GeminiProvider does live
schema-enforced structured output (validation + one retry). FixtureProvider is
a deterministic policy engine implementing the same contracts, so the full demo
and eval suite run keyless, offline, and reproducibly. This also gives evals a
stable baseline: behaviour tests assert policy outcomes, not prose.
