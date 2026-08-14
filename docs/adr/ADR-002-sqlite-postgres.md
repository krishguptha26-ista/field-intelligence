# ADR-002: SQLite (dev) behind SQLAlchemy, Postgres-ready
Status: accepted (POC)

DATABASE_URL switches to postgresql+psycopg with no code change. The production
spec calls for Postgres + pgvector; the POC needs zero-setup portability so an
evaluator can run it in 90 seconds. Embedding/vector retrieval is deferred —
standards retrieval at POC scale is exact/category-based, and embeddings must
never be the source of truth anyway.
