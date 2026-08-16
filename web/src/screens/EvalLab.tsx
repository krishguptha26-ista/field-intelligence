import { useEffect, useState } from "react";
import { api } from "../api";

export default function EvalLab() {
  const [data, setData] = useState<any>(null);
  useEffect(() => { api.evals().then(setData).catch(() => {}); }, []);
  if (!data) return <div><h1>Eval Lab</h1><div className="card">Loading…</div></div>;
  if (!data.ran) return <div><h1>Eval Lab</h1><div className="card">{data.note}</div></div>;

  const gate = data.gate;
  const colour = (c: any) => c.skipped
    ? "var(--stone-500)"
    : c.passed ? "var(--verified)" : c.flaky ? "var(--amber)" : "var(--risk)";

  return (
    <div>
      <h1>Eval Lab</h1>
      <div className="sub">
        Golden behaviour cases run against the live pipeline. Each case runs{" "}
        {data.repeats}× — because a single run of a non-deterministic system tells you
        almost nothing, and a case that passes sometimes is a finding, not a re-run.
        The release gate is the unsupported-finding rate, never answer similarity.
      </div>

      <div className="row">
        <div className="card stat">
          <div className="n">{data.passed}/{data.total}</div>
          <div className="l">passed every executed run</div>
        </div>
        <div className="card stat">
          <div className="n" style={{ color: data.flaky ? "var(--amber)" : undefined }}>
            {data.flaky}
          </div>
          <div className="l">flaky (not unanimous)</div>
        </div>
        <div className="card stat">
          <div className="n" style={{ color: data.skipped ? "var(--stone-500)" : undefined }}>
            {data.skipped ?? 0}
          </div>
          <div className="l">skipped (never counted as pass)</div>
        </div>
        <div className="card stat">
          <div className="n">{(data.mean_pass_rate * 100).toFixed(0)}%</div>
          <div className="l">mean executed pass rate</div>
        </div>
        <div className="card stat">
          <div className="n" style={{ fontSize: 15 }}>
            {data.provider?.active_provider ?? "—"}
          </div>
          <div className="l">provider under test</div>
        </div>
      </div>

      {gate && (
        <div className="card" style={{ borderLeft: `3px solid ${gate.passed ? "var(--verified)" : "var(--risk)"}` }}>
          <span className={gate.passed ? "pass" : "fail"}>
            RELEASE GATE — {gate.passed ? "CLEAR" : "BLOCKED"}
          </span>
          <div className="notice mono" style={{ marginTop: 4 }}>{gate.detail}</div>
          <div className="notice">
            A finding that reaches a reviewer without evidence, without a cited standard, or
            citing one the agent never retrieved is a failure regardless of how plausible it reads.
          </div>
        </div>
      )}

      <div className="notice" style={{ margin: "14px 0 6px" }}>
        Last run {new Date(data.at).toLocaleString()} · {data.provider?.reason}
        {data.system_under_test?.build_fingerprint && (
          <> · build <span className="mono">{data.system_under_test.build_fingerprint.slice(0, 8)}</span></>
        )}
        {data.artifact?.git_commit && data.artifact.git_commit !== "unknown" && (
          <> · commit <span className="mono">{data.artifact.git_commit.slice(0, 8)}</span>
            {data.artifact.git_dirty ? " + uncommitted changes" : ""}</>
        )}
      </div>
      {data.artifact?.delivery === "packaged_build_fixture" && (
        <div className="notice" style={{ margin: "0 0 14px" }}>
          <b>Build-time fixture evaluation.</b> This artifact was generated from the
          source packaged in the deployed image. It is not presented as a live Gemini
          or live-vision run. {data.artifact?.scope}
        </div>
      )}

      {data.cases.map((c: any) => (
        <div key={c.id} className="card" style={{ borderLeft: `3px solid ${colour(c)}` }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <div>
              <span className={c.passed ? "pass" : c.skipped ? "" : "fail"}>
                {c.status ?? (c.passed ? "PASS" : c.flaky ? "FLAKY" : "FAIL")}
              </span>
              &nbsp;<b>{c.name}</b>
            </div>
            <span className="mono" style={{ color: "var(--stone-500)" }}>
              {c.skipped
                ? `${c.attempts ?? 1} skipped attempt${(c.attempts ?? 1) === 1 ? "" : "s"}`
                : `${(c.pass_rate * 100).toFixed(0)}% over ${c.runs} run${c.runs === 1 ? "" : "s"}`}
            </span>
          </div>
          <div className="notice mono" style={{ marginTop: 4 }}>{c.detail}</div>
          {c.all_details?.length > 0 && (
            <details className="panel-block">
              <summary>Per-run detail (this case did not behave identically each run)</summary>
              {c.all_details.map((d: string, i: number) => (
                <div key={i} className="mono trace-step">run {i + 1}: {d}</div>
              ))}
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
