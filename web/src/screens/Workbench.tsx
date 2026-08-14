import { useEffect, useState } from "react";
import { api } from "../api";
import type { Ctx } from "../App";
import { Prov } from "../App";

export default function Workbench({ ctx }: { ctx: Ctx }) {
  const [audit, setAudit] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => { if (ctx.auditId) setAudit(await api.getAudit(ctx.auditId)); };
  useEffect(() => { refresh().catch(() => {}); }, [ctx.auditId]);

  const act = async (fid: string, action: string, edits?: any, reason = "") => {
    setBusy(true);
    try { await api.review(fid, action, ctx.role, reason, edits); await refresh(); }
    finally { setBusy(false); }
  };

  if (!ctx.auditId || !audit) return (
    <div><h1>Finding workbench</h1>
      <div className="card">No audit session selected. Start one in <b>Live audit</b> first.</div></div>
  );

  return (
    <div>
      <h1>Finding workbench</h1>
      <div className="sub">
        The model proposes; a human decides. Approval is the only path to a corrective action, and every decision is recorded.
      </div>

      {audit.findings.length === 0 && <div className="card">No candidate findings yet — run the agent analysis in Live audit.</div>}

      {audit.findings.map((f: any) => {
        const high = f.severity === "HIGH" || f.severity === "CRITICAL";
        return (
          <div key={f.id} className={`card fcard ${f.status} ${high && f.status === "APPROVED" ? "HIGHSEV" : ""}`}>
            <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
              <div><span className={`sev ${f.severity}`}>{f.severity}</span> &nbsp;<b>{f.title}</b></div>
              <span className={`badge ${f.status === "APPROVED" ? "ok" : f.status === "DISPUTED" ? "signal" : "amber"}`}>{f.status}</span>
            </div>

            {f.recurrence?.closed_and_verified && (
              <div className="recurrence">
                <b>Repeat issue.</b> {f.recurrence.summary}
                <div className="notice" style={{ marginTop: 4 }}>
                  Previous corrective action: "{f.recurrence.corrective_action}" — signed off
                  {" "}{f.recurrence.days_since_prior} days ago. Severity was raised one level
                  automatically; the reason is recorded in the uncertainty list below.
                </div>
              </div>
            )}

            <dl className="kv" style={{ marginTop: 10 }}>
              <dt>Consultant said</dt><dd>"{f.consultant_statement}"</dd>
              <dt>Model interpretation</dt><dd>{f.model_interpretation}</dd>
              <dt>Applicable standard</dt>
              <dd>{f.standard ? <><b>{f.standard.code}</b> — {f.standard.text}</> : <i>none cited</i>}
                  &nbsp;<span className="badge fixture">representative demo standard</span></dd>
              <dt>Evidence</dt>
              <dd>{f.evidence.map((e: any) => e && (
                <div key={e.id}><Prov p={e.provenance} /> <span className="mono">{e.id}</span> — "{e.excerpt}"</div>
              ))}</dd>
              <dt>Confidence</dt><dd>{(f.confidence * 100).toFixed(0)}%</dd>
              <dt>Uncertainty</dt><dd>{f.uncertainty_reasons.join("; ") || "—"}</dd>
              <dt>Evidence does NOT support</dt>
              <dd className="notice">{f.not_supported.join("; ") || "—"}</dd>
              <dt>Draft corrective action</dt>
              <dd>{f.recommended_action?.description}<br />
                <span className="notice">Owner: {f.recommended_action?.owner_role} · due {f.recommended_action?.suggested_due_date} · verify: {f.recommended_action?.verification_method}</span></dd>
            </dl>

            {f.challenge_record?.ran && (
              <details className="panel-block">
                <summary>
                  <b>Challenge panel</b> — three independent challengers argued against this
                  finding before you saw it. Outcome:{" "}
                  <span className={`badge ${f.challenge_record.outcome === "UPHELD" ? "ok" : "amber"}`}>
                    {f.challenge_record.outcome}
                  </span>{" "}
                  <span className="mono">
                    ({f.challenge_record.votes?.uphold ?? 0} uphold ·{" "}
                    {f.challenge_record.votes?.weaken ?? 0} weaken ·{" "}
                    {f.challenge_record.votes?.overturn ?? 0} overturn)
                  </span>
                </summary>
                {f.challenge_record.challenges?.map((c: any) => (
                  <div key={c.lens} className="challenge">
                    <div>
                      <span className={`badge ${c.verdict === "UPHOLD" ? "ok" : c.verdict === "OVERTURN" ? "signal" : "amber"}`}>
                        {c.verdict}
                      </span>{" "}
                      <b>{c.lens.replace(/_/g, " ")}</b>
                    </div>
                    <div style={{ marginTop: 4 }}>{c.argument}</div>
                    {c.specific_gap && <div className="notice">Gap: {c.specific_gap}</div>}
                    {c.what_would_settle_it && (
                      <div className="notice">Would settle it: {c.what_would_settle_it}</div>
                    )}
                  </div>
                ))}
              </details>
            )}

            {f.reasoning_trace?.length > 0 && (
              <details className="panel-block">
                <summary>
                  <b>How the agent investigated</b> — {f.reasoning_trace.length} read-only tool
                  call{f.reasoning_trace.length === 1 ? "" : "s"} before it proposed anything
                </summary>
                {f.reasoning_trace.map((t: any, i: number) => (
                  <div key={i} className="trace-step">
                    <div>
                      <span className="badge">{t.tool}</span>{" "}
                      {t.actor === "SYSTEM_FALLBACK" && (
                        <span className="badge fixture">system fallback — agent requested no retrieval</span>
                      )}
                    </div>
                    <div className="mono">args: {JSON.stringify(t.args)}</div>
                    <div className="mono" style={{ color: "var(--stone-500)" }}>
                      returned: {JSON.stringify(t.result).slice(0, 260)}
                      {JSON.stringify(t.result).length > 260 ? "…" : ""}
                    </div>
                  </div>
                ))}
              </details>
            )}

            {f.status === "READY_FOR_REVIEW" && (
              <div className="pill-options" style={{ marginTop: 10 }}>
                <button className="primary" disabled={busy} onClick={() => act(f.id, "approve")}>Approve</button>
                <button className="ghost" disabled={busy} onClick={() => {
                  const sev = prompt("Edit severity (INFO/LOW/MEDIUM/HIGH/CRITICAL):", f.severity);
                  if (sev) act(f.id, "edit_approve", { severity: sev.toUpperCase() }, "severity adjusted by reviewer");
                }}>Edit & approve</button>
                <button className="ghost" disabled={busy} onClick={() => {
                  const r = prompt("Rejection reason:") ?? "";
                  if (r) act(f.id, "reject", undefined, r);
                }}>Reject</button>
                <button className="ghost" disabled={busy} onClick={() => {
                  const r = prompt("Dispute reason / contrary evidence:") ?? "";
                  if (r) act(f.id, "dispute", undefined, r);
                }}>Dispute</button>
              </div>
            )}

            {f.review_history.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="notice">Decision trail (append-only)</div>
                {f.review_history.map((h: any, i: number) => (
                  <div key={i} className="mono" style={{ color: "var(--stone-500)" }}>
                    {h.at} · {h.actor} · {h.action}{h.reason ? ` — "${h.reason}"` : ""}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {audit.actions.length > 0 && (
        <>
          <h2>Corrective actions</h2>
          {audit.actions.map((a: any) => (
            <div key={a.id} className="card">
              <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
                <b>{a.description}</b>
                <span className={`badge ${a.status === "VERIFIED" ? "ok" : "amber"}`}>{a.status}</span>
              </div>
              <div className="notice">Owner: {a.owner_role} · due {a.due_date} · verification: {a.verification_method}</div>
              {a.status !== "VERIFIED" && (
                <button className="ghost" style={{ marginTop: 8 }} onClick={async () => {
                  await api.verifyAction(a.id, "After photo reviewed — condition corrected (simulated for demo)");
                  await refresh();
                }}>Record verification evidence (simulated)</button>
              )}
              {a.events.map((e: any, i: number) => (
                <div key={i} className="mono" style={{ color: "var(--stone-500)" }}>
                  {e.at} · {e.event} · {e.by}{e.provenance ? " · " : ""}{e.provenance && <Prov p={e.provenance} />}
                </div>
              ))}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
