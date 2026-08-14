# ADR-004: Evidence/inference separation and human approval
Status: accepted

Observations (what the consultant recorded), evidence items (envelope with
provenance + trust class), model interpretation (kept separate from the
consultant's words), findings (candidate until a human approves), actions
(exist only after approval), audit log (append-only). Public signals are a
separate table and can never be cited as finding evidence. A deterministic
policy layer re-checks every model decision (vague wording without stated
uncertainty is demoted to clarification).
