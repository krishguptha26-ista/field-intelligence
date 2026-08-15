import { useEffect, useState } from "react";
import { api } from "../api";
import type { Ctx } from "../App";
import { Prov } from "../App";

export default function Benchmark({ ctx }: { ctx: Ctx }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setData(null); setError("");
    api.benchmark(ctx.locationId).then(setData).catch(e => setError(String(e)));
  }, [ctx.locationId]);

  if (error) return <div><h1>Competitive edge</h1><div className="card">{error}</div></div>;
  if (!data) return <div><h1>Competitive edge</h1><div className="card">Loading…</div></div>;

  return <div>
    <h1>Competitive edge</h1>
    <div className="sub">What guests consistently praise elsewhere, where Wolf Creek already leads, and which operating changes are worth testing.</div>
    <div className="card">
      <div className="ticket-head"><b>Benchmark contract</b><Prov p={data.provenance} /></div>
      <p className="notice">{data.method}</p>
      <div className="metric-grid">
        {data.cohort.map((course: any) => <div className="metric" key={course.id}>
          <b>{course.total_reviews}</b><span>{course.name}</span>
          <small>{course.positive_written_reviews} positive written reviews analysed</small>
        </div>)}
      </div>
    </div>

    <h2>Opportunity ranking</h2>
    <div className="card table-scroll">
      <table>
        <thead><tr><th>Guest value</th><th>Wolf Creek</th><th>Peer median</th><th>Leader</th><th>Read</th></tr></thead>
        <tbody>{data.comparisons.map((row: any) => <tr key={row.key}>
          <td><b>{row.label}</b><div className="notice">{row.subject_mentions} supporting mentions</div></td>
          <td className="mono">{row.subject_rate}/100</td>
          <td className="mono">{row.peer_median_rate}/100</td>
          <td>{row.leader}<div className="notice">{row.leader_rate}/100 · {row.leader_mentions} mentions</div></td>
          <td><span className={`badge ${row.classification === "RELATIVE_STRENGTH" ? "ok" : row.classification === "OPPORTUNITY" ? "amber" : "neutral"}`}>
            {row.classification.replace(/_/g, " ")}
          </span></td>
        </tr>)}</tbody>
      </table>
    </div>

    <h2>Recommended experiments</h2>
    {data.recommendations.length === 0 && <div className="card">No supported gap clears the minimum threshold in this cohort.</div>}
    {data.recommendations.map((row: any, index: number) => <div className="card" key={row.key} style={{ borderLeft: "3px solid var(--gold)" }}>
      <div className="ticket-head"><b>{index + 1}. {row.label}</b><span className="badge amber">{row.gap_to_leader_pp}pp gap to leader</span></div>
      <p>{row.recommendation}</p>
      <div className="notice">Benchmark leader: {row.leader}. Based on {row.leader_mentions} positive mentions; {row.leader_evidence_refs.length} anonymized evidence references retained for auditability.</div>
    </div>)}
  </div>;
}
