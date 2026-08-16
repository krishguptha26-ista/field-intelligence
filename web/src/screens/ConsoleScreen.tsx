import { useEffect, useState } from "react";
import { api } from "../api";

export default function ConsoleScreen() {
  const [data, setData] = useState<any>(null);
  useEffect(() => { api.console().then(setData).catch(() => {}); }, []);
  if (!data) return <div><h1>Cost & observability</h1><div className="card">Loading…</div></div>;
  const t = data.totals;
  const access = data.access_activity ?? { successful_logins: 0, webhook_configured: false, recent: [] };
  return (
    <div>
      <h1>Cost & observability</h1>
      <div className="sub">Every model call is a ledger entry: purpose, tokens, latency, estimated cost, retries. Prices come from config, not code.</div>
      <div className="row">
        <div className="card stat"><div className="n">{t.calls}</div><div className="l">model calls</div></div>
        <div className="card stat"><div className="n">{t.audits}</div><div className="l">audit sessions</div></div>
        <div className="card stat"><div className="n">${t.est_cost_usd}</div><div className="l">est. total cost</div></div>
        <div className="card stat"><div className="n">${t.est_cost_per_audit}</div><div className="l">est. cost / audit</div></div>
        <div className="card stat"><div className="n">{t.avg_latency_ms}ms</div><div className="l">avg latency</div></div>
      </div>
      <div className="card">
        <table>
          <thead><tr><th>Purpose</th><th>Provider</th><th>In</th><th>Out</th><th>Latency</th><th>Cost</th><th>Retries</th><th>OK</th></tr></thead>
          <tbody>
            {data.recent.map((c: any, i: number) => (
              <tr key={i}>
                <td>{c.purpose}</td>
                <td><span className={`badge ${c.provider === "gemini" ? "live" : "fixture"}`}>{c.provider}</span></td>
                <td className="mono">{c.in}</td><td className="mono">{c.out}</td>
                <td className="mono">{c.latency_ms}ms</td><td className="mono">${c.cost}</td>
                <td className="mono">{c.retries}</td>
                <td>{c.ok ? <span className="pass">✓</span> : <span className="fail">✗</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Demo access activity</h2>
      <div className="sub">Successful sign-ins only. Visitor IDs are one-way pseudonyms; raw IP addresses and passwords are not retained.</div>
      <div className="row">
        <div className="card stat"><div className="n">{access.successful_logins}</div><div className="l">successful sign-ins</div></div>
        <div className="card stat"><div className="n">{access.webhook_configured ? "ON" : "OFF"}</div><div className="l">private login notification</div></div>
      </div>
      <div className="card">
        {access.recent.length === 0 ? <p className="muted">No successful sign-ins recorded yet.</p> : <table>
          <thead><tr><th>When</th><th>User</th><th>Anonymous visitor</th><th>Browser</th><th>Notification</th></tr></thead>
          <tbody>{access.recent.map((event: any) => (
            <tr key={`${event.at}-${event.visitor_id}`}>
              <td>{new Date(event.at).toLocaleString()}</td>
              <td>{event.username}</td>
              <td className="mono">{event.visitor_id}</td>
              <td title={event.user_agent}>{event.user_agent.length > 64 ? `${event.user_agent.slice(0, 61)}…` : event.user_agent}</td>
              <td><span className={`badge ${event.notification_status === "SENT" ? "ok" : event.notification_status === "FAILED" ? "risk" : "amber"}`}>
                {event.notification_status.replaceAll("_", " ")}
              </span></td>
            </tr>
          ))}</tbody>
        </table>}
      </div>
    </div>
  );
}
