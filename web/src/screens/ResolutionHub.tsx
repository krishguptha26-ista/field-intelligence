import { useEffect, useState } from "react";
import { api } from "../api";
import type { Ctx } from "../App";
import { Prov } from "../App";

function TicketCard({ ticket, actor, refresh }: { ticket: any; actor: string; refresh: () => Promise<void> }) {
  const [note, setNote] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (fn: () => Promise<any>) => {
    setBusy(true); setError("");
    try { await fn(); setNote(""); setFile(null); await refresh(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  const upload = (stage: "BEFORE" | "AFTER") => {
    if (!file || note.trim().length < 3) {
      setError("Choose a photo and add a short evidence note."); return;
    }
    run(() => api.uploadTicketEvidence(ticket.id, stage, file, note, actor));
  };

  return (
    <div className="card" style={{ borderLeft: `3px solid ${ticket.priority === "HIGH" ? "var(--risk)" : "var(--gold)"}` }}>
      <div className="ticket-head">
        <div>
          <span className={`sev ${ticket.priority}`}>{ticket.priority}</span>
          <b style={{ marginLeft: 8 }}>{ticket.title}</b>
        </div>
        <span className={`badge ${ticket.status === "CLOSED_VERIFIED" ? "ok" : "amber"}`}>
          {ticket.status.replace(/_/g, " ")}
        </span>
      </div>
      <div className="notice" style={{ marginTop: 6 }}>
        Assigned to <b>{ticket.assigned_role}</b> · due {ticket.due_date} · {ticket.source_refs.length} supporting review(s)
      </div>
      <p>{ticket.description}</p>
      <div>
        <span className="badge neutral">validity: {ticket.validity_status.replace(/_/g, " ")}</span>
        <span className="badge neutral">before: {ticket.before_evidence.length}</span>
        <span className="badge neutral">after: {ticket.after_evidence.length}</span>
      </div>

      {ticket.status !== "CLOSED_VERIFIED" && ticket.status !== "DISMISSED" && (
        <div className="evidence-controls">
          <input type="file" accept="image/jpeg,image/png,image/webp"
                 onChange={e => setFile(e.target.files?.[0] ?? null)} />
          <input value={note} onChange={e => setNote(e.target.value)}
                 placeholder="What does this evidence establish?" />
        </div>
      )}

      <div className="pill-options" style={{ marginTop: 10 }}>
        {ticket.status === "PENDING_VALIDATION" && <>
          <button className="ghost" disabled={busy} onClick={() => upload("BEFORE")}>Upload before photo</button>
          <button className="primary" disabled={busy} onClick={() => run(() => api.validateTicket(
            ticket.id, "VALIDATED_ON_SITE", actor, note || "Condition confirmed during on-site inspection"))}>Validate on site</button>
          <button className="ghost" disabled={busy} onClick={() => run(() => api.validateTicket(
            ticket.id, "NOT_SUBSTANTIATED", actor, note || "Condition was not present during on-site inspection"))}>Not substantiated</button>
        </>}
        {ticket.status === "OPEN" && <>
          <button className="ghost" disabled={busy} onClick={() => upload("AFTER")}>Upload after photo</button>
          <button className="primary" disabled={busy} onClick={() => run(() => api.resolveTicket(
            ticket.id, actor, note || "Corrective work completed; after evidence attached"))}>Submit resolution</button>
        </>}
        {ticket.status === "RESOLVED_PENDING_VERIFICATION" &&
          <button className="primary" disabled={busy} onClick={() => run(() => api.verifyTicket(
            ticket.id, actor, note || "Resolution and after evidence independently reviewed"))}>Manager verify</button>}
        {ticket.status === "CLOSED_VERIFIED" && !ticket.external_reply?.comment &&
          <button className="ghost" disabled={busy} onClick={() => run(() => api.draftTicketReply(ticket.id))}>Draft public owner reply</button>}
      </div>
      {error && <div className="error-box" role="alert">{error}</div>}

      {ticket.external_reply?.comment && (
        <div className="reply-draft">
          <b>Public owner reply draft</b> <Prov p="AWAITING_BUSINESS_PROFILE_AUTH" />
          <p>{ticket.external_reply.comment}</p>
          <div className="notice">{ticket.external_reply.note}</div>
        </div>
      )}
      <details style={{ marginTop: 10 }}>
        <summary>Decision and evidence trail ({ticket.events.length})</summary>
        {ticket.events.map((event: any, i: number) => (
          <div key={i} className="mono">{event.at} · {event.event} · {event.by}{event.note ? ` — ${event.note}` : ""}</div>
        ))}
      </details>
    </div>
  );
}

function LearningQueue({ proposals, actor, refresh }: { proposals: any[]; actor: string; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const decide = async (id: string, decision: "APPROVE" | "REJECT") => {
    setBusy(id); setError("");
    try {
      await api.decideTaxonomy(id, decision, actor,
        decision === "APPROVE" ? "Recurring language merits standards-owner design review" : "Existing taxonomy already covers this need");
      await refresh();
    } catch (e) { setError(String(e)); }
    finally { setBusy(""); }
  };
  return <div className="card">
    <div className="ticket-head"><b>Human-governed learning queue</b><Prov p="CUSTOMER_SIGNAL_CONTEXT" /></div>
    <p className="notice">Recurring language can propose a new measurable parameter. Nothing silently retrains, rewrites standards, or changes production behaviour.</p>
    {error && <div className="error-box" role="alert">{error}</div>}
    {proposals.length === 0 && <div className="notice">No candidate parameters yet. Sync recurring feedback to generate reviewable suggestions.</div>}
    {proposals.map((proposal) => <div className="panel-block" key={proposal.id}>
      <div className="ticket-head">
        <div><b>{proposal.label}</b> <span className="mono">{proposal.proposed_key}</span></div>
        <span className={`badge ${proposal.status === "APPROVED_FOR_DESIGN" ? "ok" : "neutral"}`}>{proposal.status.replace(/_/g, " ")}</span>
      </div>
      <p>{proposal.rationale}</p>
      <div className="notice">{proposal.example_refs.length} anonymized examples · {proposal.effect}</div>
      {proposal.status === "PENDING_REVIEW" && <div className="pill-options">
        <button className="primary" disabled={busy === proposal.id} onClick={() => decide(proposal.id, "APPROVE")}>Approve for design</button>
        <button className="ghost" disabled={busy === proposal.id} onClick={() => decide(proposal.id, "REJECT")}>Reject</button>
      </div>}
    </div>)}
  </div>;
}

export default function ResolutionHub({ ctx }: { ctx: Ctx }) {
  const [tickets, setTickets] = useState<any[]>([]);
  const [proposals, setProposals] = useState<any[]>([]);
  const [analytics, setAnalytics] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    const [ticketData, metrics, taxonomy] = await Promise.all([
      api.tickets(ctx.locationId), api.resolutionAnalytics(ctx.locationId), api.taxonomy(ctx.locationId),
    ]);
    setTickets(ticketData.tickets); setAnalytics(metrics); setProposals(taxonomy.proposals);
  };

  useEffect(() => { setError(""); refresh().catch(e => setError(String(e))); }, [ctx.locationId]);

  const sync = async () => {
    setBusy(true); setError("");
    try {
      // The taxonomy call reuses the server's completed theme analysis. Keeping
      // first-use calls sequential avoids duplicate model work and cost.
      await api.syncTickets(ctx.locationId);
      await api.syncTaxonomy(ctx.locationId);
      await refresh();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <h1>Resolution hub</h1>
      <div className="sub">Customer signals become assigned triage—not automatic violations. Staff validate, attach before/after proof, resolve, and a manager verifies the outcome.</div>
      <button className="primary" disabled={busy} onClick={sync}>{busy ? "Analysing recurring signals…" : "Sync recurring feedback"}</button>
      {error && <div className="error-box" role="alert">{error}</div>}

      {analytics && <>
        <div className="metric-grid">
          <div className="metric"><b>{analytics.tickets.open}</b><span>requiring attention</span></div>
          <div className="metric"><b>{analytics.tickets.closed_verified}</b><span>verified closed</span></div>
          <div className="metric"><b>{analytics.tickets.mean_time_to_verified_hours ?? "—"}</b><span>mean hours to verify</span></div>
          <div className="metric"><b>{analytics.rating_impact.state.replace(/_/g, " ")}</b><span>impact measurement</span></div>
        </div>
        <div className="card">
          <b>Rating impact: honest baseline</b>
          <div className="notice">{analytics.rating_impact.claim}</div>
          <div className="funnel">
            <span>{analytics.rating_impact.baseline.total ?? 0} collected</span>
            <span>→</span><span>{analytics.rating_impact.baseline.recent_all_ratings ?? "—"} recent</span>
            <span>→</span><span>{analytics.rating_impact.baseline.recent_low_rating_written ?? "—"} actionable written</span>
          </div>
        </div>
      </>}

      <LearningQueue proposals={proposals} actor={ctx.role} refresh={refresh} />

      <h2>Assigned operational tickets</h2>
      {tickets.length === 0 && <div className="card">No tickets yet. Sync recurring feedback to create idempotent, assigned triage work.</div>}
      {tickets.map(ticket => <TicketCard key={ticket.id} ticket={ticket} actor={ctx.role} refresh={refresh} />)}
    </div>
  );
}
