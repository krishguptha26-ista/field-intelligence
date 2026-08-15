import { useEffect, useState } from "react";
import { api } from "../api";
import type { Ctx } from "../App";
import { Prov } from "../App";

export default function Signals({ ctx }: { ctx: Ctx }) {
  const [data, setData] = useState<any>(null);
  const [sources, setSources] = useState<any>(null);
  const [err, setErr] = useState("");
  const [loadingSeconds, setLoadingSeconds] = useState(0);

  useEffect(() => {
    setData(null); setSources(null); setErr(""); setLoadingSeconds(0);
    api.signals(ctx.locationId).then(setData).catch(e => setErr(String(e)));
    // Loaded separately: the source panel is diagnostic, and a slow or failing
    // source must not hold up the page it is diagnosing.
    api.sources(ctx.locationId).then(setSources).catch(() => {});
  }, [ctx.locationId]);

  useEffect(() => {
    if (data || err) return;
    const started = Date.now();
    const timer = window.setInterval(
      () => setLoadingSeconds(Math.floor((Date.now() - started) / 1000)), 1000,
    );
    return () => window.clearInterval(timer);
  }, [data, err, ctx.locationId]);

  if (err) return <div><h1>Customer signals</h1><div className="card">{err}</div></div>;
  if (!data) return <div>
    <h1>Customer signals</h1>
    <div className="card" role="status" aria-live="polite">
      <b>Preparing review intelligence…</b>
      <p>Checking the saved review snapshot, corroborating location sources, and grouping recurring themes. A first live analysis can take around 30 seconds.</p>
      <div className="notice">{loadingSeconds < 10
        ? "Starting the evidence pipeline…"
        : `Still working — ${loadingSeconds}s elapsed. No review data has been lost.`}</div>
    </div>
  </div>;

  const { sample, themes } = data;

  return (
    <div>
      <h1>Customer signals</h1>
      <div className="sub">
        Public review context beside — never inside — the audit. Signals corroborate or raise questions; they cannot create findings.
      </div>

      <div className="card" style={{ borderLeft: "3px solid var(--signal)" }}>
        <b>Review intelligence scope</b>
        <div style={{ marginTop: 6 }}>
          <span className="badge signal">{sample.reviews.length} recent low-rating written reviews</span>
          <Prov p={sample.provenance} />
          <span className="badge neutral">window ≈ {sample.window_days} days</span>
        </div>
        <div className="notice" style={{ marginTop: 6 }}>{sample.sample_caveat}</div>
        {sample.dataset_summary && (
          <div className="funnel" aria-label="review filtering funnel">
            <span><b>{sample.dataset_summary.total ?? sample.dataset_summary.source_rows_available}</b> collected</span>
            <span>→</span>
            <span><b>{sample.dataset_summary.recent_all_ratings ?? "—"}</b> within {sample.window_days} days</span>
            <span>→</span>
            <span><b>{sample.dataset_summary.recent_low_rating ?? "—"}</b> rated ≤3★</span>
            <span>→</span>
            <span><b>{sample.dataset_summary.recent_low_rating_written}</b> with written evidence</span>
          </div>
        )}
        {sample.selection && <div className="mono" style={{ marginTop: 8 }}>Filter: {sample.selection}</div>}
        {sample.location_meta?.rating && (
          <div className="notice">Listing rating {sample.location_meta.rating} from {sample.location_meta.rating_count} total ratings (the sample below is NOT those {sample.location_meta.rating_count}).</div>
        )}
      </div>

      {sources && (
        <div className="card">
          <b>Where this came from</b>
          <div className="notice" style={{ marginTop: 4 }}>
            Every source is queried in parallel and ranked by how much we can trust that the
            data is what it claims to be. Rank never turns sentiment into proof — the highest-trust
            review in the world is still context. {sources.answered} of {sources.attempted} sources
            answered.
          </div>
          <table style={{ marginTop: 8 }}>
            <thead><tr><th>Source</th><th>Trust</th><th>Status</th><th>Provenance</th><th>Latency</th></tr></thead>
            <tbody>
              {sources.sources.map((s: any) => (
                <tr key={s.source_id}>
                  <td><b>{s.source_id}</b>{s.attribution && <div className="notice">{s.attribution}</div>}</td>
                  <td><span className="badge neutral">{s.trust_class} ({s.trust_rank})</span></td>
                  <td>
                    <span className={`badge ${s.ok ? "ok" : "amber"}`}>{s.ok ? "answered" : "no data"}</span>
                    {s.error && <div className="notice">{s.error}</div>}
                  </td>
                  <td><Prov p={s.provenance} /></td>
                  <td className="mono">{s.latency_ms}ms</td>
                </tr>
              ))}
              {sources.skipped.map((s: any) => (
                <tr key={s.source_id}>
                  <td><b>{s.source_id}</b><div className="notice">{s.note}</div></td>
                  <td><span className="badge neutral">{s.trust_class}</span></td>
                  <td><span className="badge fixture">skipped</span><div className="notice">{s.reason}</div></td>
                  <td>—</td><td className="mono">—</td>
                </tr>
              ))}
            </tbody>
          </table>
          {sources.corroboration?.notes?.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <b>Cross-source checks</b>
              {sources.corroboration.notes.map((n: any, i: number) => (
                <div key={i} style={{ marginTop: 6 }}>
                  <span className="badge neutral">{n.type.replace(/_/g, " ")}</span>
                  <div className="notice">{n.detail}</div>
                  {n.osm_url && (
                    <a className="mono" href={n.osm_url} target="_blank" rel="noreferrer">{n.osm_url}</a>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <h2>Recurring negative themes (last ~3 months)</h2>
      {themes.themes.length === 0 && <div className="card">No recurring negative themes in the current sample. {themes.anecdotes.length > 0 ? "Single mentions listed below." : ""}</div>}
      {themes.themes.map((t: any, i: number) => (
        <div key={i} className="card" style={{ borderLeft: "3px solid var(--signal)" }}>
          <b>{t.theme}</b> <span className="badge signal">{t.mention_count} mentions</span>
          {t.linked_categories.map((l: any, j: number) => (
            <div key={j} style={{ marginTop: 6 }}>
              <span className="badge neutral">{l.category}</span>
              <span className="notice"> {l.language}</span>
            </div>
          ))}
        </div>
      ))}

      {themes.anecdotes.length > 0 && (
        <div className="card">
          <b>Single mentions (not themes)</b>
          {themes.anecdotes.map((a: string, i: number) => <div key={i} className="notice">• {a}</div>)}
        </div>
      )}

      <h2>Recent low-rating written reviews</h2>
      <div className="card table-scroll">
        <table>
          <thead><tr><th>Rating</th><th>Review</th><th>Age</th><th>Provenance</th></tr></thead>
          <tbody>
            {sample.reviews.map((r: any) => (
              <tr key={r.id}>
                <td><span className={`sev ${r.rating <= 2 ? "HIGH" : r.rating === 3 ? "MEDIUM" : "LOW"}`}>{r.rating}★</span></td>
                <td>{r.text}</td>
                <td className="notice">{r.days_ago != null ? `${r.days_ago}d ago` : "—"}</td>
                <td><Prov p={r.provenance} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
