import { useEffect, useState } from "react";
import { api } from "../api";
import type { Ctx } from "../App";
import { Prov } from "../App";

export default function Portfolio({ ctx, goto }: { ctx: Ctx; goto: (s: string) => void }) {
  const [sim, setSim] = useState<any>(null);
  const [dt, setDt] = useState<any>(null);
  const [resetting, setResetting] = useState(false);

  useEffect(() => { api.simulated().then(setSim).catch(() => {}); }, []);
  useEffect(() => {
    setDt(null);
    fetch(`/api/locations/${ctx.locationId}/digital-truth`).then(r => r.json()).then(setDt).catch(() => {});
  }, [ctx.locationId]);

  return (
    <div>
      <h1>Portfolio pulse</h1>
      <div className="sub">One governed evidence loop across every property — golf today, any operation tomorrow.</div>

      <div className="row">
        {ctx.tenants.map(t => t.locations.map((l: any) => (
          <div key={l.id} className="card" style={{ cursor: "pointer", outline: l.id === ctx.locationId ? "1.5px solid var(--gold)" : "none" }}
               onClick={() => ctx.setLocation(t.id, l.id)}>
            <div style={{ fontWeight: 700 }}>{l.name}</div>
            <div className="notice" style={{ marginBottom: 8 }}>{l.address}</div>
            <div>
              <span className="badge neutral">{t.kind}</span>
              {l.meta?.fixture ? <Prov p="DEMO_FIXTURE" /> : <span className="badge ok">reference tenant</span>}
            </div>
            <div style={{ marginTop: 10 }}>
              <button className="ghost" onClick={e => { e.stopPropagation(); ctx.setLocation(t.id, l.id); goto("audit"); }}>
                Start / continue audit →
              </button>
            </div>
          </div>
        )))}
      </div>

      {dt && dt.conflicts?.length > 0 && (
        <>
          <h2>Digital truth monitor</h2>
          <div className="card" style={{ borderLeft: "3px solid var(--gold)" }}>
            <span className="badge amber">Opportunity — not a violation</span>
            <Prov p={dt.provenance} />
            <span className="badge neutral">verified {dt.verified_at}</span>
            <p style={{ margin: "8px 0 4px" }}><b>{dt.title}</b></p>
            <table>
              <thead><tr><th>Attribute</th><th>Channel says</th><th>Source</th></tr></thead>
              <tbody>
                {dt.conflicts.map((c: any) => c.values.map((v: any, i: number) => (
                  <tr key={c.attribute + i}>
                    <td>{i === 0 ? <b>{c.attribute}</b> : ""}</td>
                    <td>{v.value}</td>
                    <td className="notice"><a style={{ color: "var(--signal)" }} href={v.url} target="_blank" rel="noreferrer">{v.source}</a> · fetched {v.fetched}</td>
                  </tr>
                )))}
              </tbody>
            </table>
            <div className="notice" style={{ marginTop: 8 }}>{dt.agent_position}</div>
            <div className="notice" style={{ marginTop: 4 }}>Suggested owner: {dt.recommended_owner} · {dt.why_it_matters}</div>
          </div>
        </>
      )}

      <h2>What's live vs simulated in this demo</h2>
      <div className="card">
        <div className="notice" style={{ marginBottom: 8 }}>
          Honesty panel: every element of this demo declares its provenance. Nothing is presented as live that isn't.
        </div>
        {sim && (
          <table>
            <thead><tr><th>Element</th><th>State</th><th>Note</th></tr></thead>
            <tbody>
              {sim.elements.map((e: any, i: number) => (
                <tr key={i}>
                  <td>{e.element}</td>
                  <td><Prov p={e.state} /></td>
                  <td className="notice">{e.note ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <button className="ghost" disabled={resetting} onClick={async () => {
        setResetting(true); await api.demoReset(); location.reload();
      }}>{resetting ? "Resetting…" : "Reset demo data"}</button>
    </div>
  );
}
