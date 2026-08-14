# ADR-005: No agent framework; narrow typed tools
Status: accepted

Governed workflows need explicit control flow. One orchestrator, deterministic
tools, Pydantic contracts at every model boundary, explicit state machine
(COLLECTING → NEEDS_CLARIFICATION | READY_FOR_ANALYSIS → READY_FOR_REVIEW →
APPROVED/REJECTED/DISPUTED → ACTION → VERIFIED). Frameworks optimize demo
speed and hide control flow — the opposite of what a compliance-adjacent
product needs. The LLM never receives a browser, shell, or mutation ability.
