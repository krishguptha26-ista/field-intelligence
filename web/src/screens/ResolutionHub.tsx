import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Ctx } from "../App";
import { Prov } from "../App";
import "../reviewer.css";

type QueueFilter = "ACTIVE" | "VERIFY" | "CLOSED" | "ALL";
type SourceFilter = "CURRENT_AUDIT" | "CUSTOMER_SIGNAL" | "ALL";

const terminalStatuses = new Set(["CLOSED_VERIFIED", "DISMISSED"]);

function humanize(value?: string) {
  return (value || "Unknown").replace(/_/g, " ").toLowerCase().replace(/^./, c => c.toUpperCase());
}

function shortId(value?: string) {
  if (!value) return "not linked";
  const [prefix, suffix] = value.split("_", 2);
  return suffix ? `${prefix}_${suffix.slice(0, 8)}` : value.slice(0, 12);
}

function friendlyError(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error);
  const jsonStart = raw.indexOf("{");
  let detail = raw.replace(/^\d{3}:\s*/, "");
  if (jsonStart >= 0) {
    try { detail = JSON.parse(raw.slice(jsonStart)).detail || detail; } catch { /* plain API response */ }
  }
  const normalized = detail.toLowerCase();
  if (normalized.includes("independent verification")) {
    return "This case must be verified by someone other than the person who validated or resolved it. Hand it to the Brand Leader verification queue.";
  }
  if (normalized.includes("before photo")) return "Add a clear before photo before confirming the condition on site.";
  if (normalized.includes("after photo")) return "Add a new after photo that clearly shows the corrected condition before submitting resolution.";
  if (normalized.includes("different image")) return "The after photo must be a new image of the corrected condition, not the original before photo.";
  if (normalized.includes("already attached")) return "That photo is already attached to this case.";
  if (normalized.includes("only a validated open ticket")) return "This case is not ready for resolution yet. Validate it on site first.";
  if (normalized.includes("resolution must be submitted")) return "The operator must submit the completed resolution before independent verification.";
  return detail.replace(/^\{"detail":"?|"?\}]}?$/g, "") || "That action could not be completed. Refresh the case and try again.";
}

function formatDate(value?: string) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(date);
}

function sourceLabel(ticket: any) {
  if (ticket.source_kind === "CUSTOMER_SIGNAL_THEME") return "Customer feedback case";
  if (ticket.source_kind === "PHOTO_BACKED_FIELD_FINDING") return "Approved field finding";
  if (ticket.source_kind === "UNMAPPED_PHOTO_BACKED_FIELD_CONCERN") return "Photo-backed field concern";
  return humanize(ticket.source_kind || "Operational case");
}

function caseRefs(ticket: any) {
  const refs = ticket.source_refs || [];
  return {
    findings: refs.filter((ref: string) => ref.startsWith("finding_")),
    actions: refs.filter((ref: string) => ref.startsWith("act_")),
    observations: refs.filter((ref: string) => ref.startsWith("ob_")),
    reviews: refs.filter((ref: string) => !/^(finding_|act_|ob_|audit_)/.test(ref)),
  };
}

function EvidenceStrip({ title, rows }: { title: string; rows: any[] }) {
  if (!rows?.length) return null;
  return <section className="case-evidence-strip" aria-label={`${title} evidence`}>
    <div className="stage-label">{title} evidence · {rows.length}</div>
    <div className="case-evidence-grid">
      {rows.map((row, index) => <figure key={`${row.digest}-${index}`}>
        <img src={`/api/photos/${row.digest}`} alt={`${title} evidence ${index + 1}`} />
        <figcaption><b>{row.note || "Evidence photo"}</b><span>{row.actor || "Actor not recorded"} · {formatDate(row.at)}</span></figcaption>
      </figure>)}
    </div>
  </section>;
}

function TicketCard({ ticket, role, currentAudit, refresh }: {
  ticket: any; role: string; currentAudit: any; refresh: () => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refs = caseRefs(ticket);
  const canOperate = role === "Location Operator";
  const canVerify = role === "Brand Leader";
  const decisionActors = new Set((ticket.events || [])
    .filter((event: any) => ["VALIDATED_ON_SITE", "FINDING_REVIEW_VALIDATED", "RESOLUTION_SUBMITTED"].includes(event.event))
    .map((event: any) => String(event.by || "").trim().toLowerCase()));
  const independentActor = !decisionActors.has(role.trim().toLowerCase());
  const finding = currentAudit?.findings?.find((item: any) => refs.findings.includes(item.id));
  const action = currentAudit?.actions?.find((item: any) => refs.actions.includes(item.id) || item.finding_id === finding?.id);

  const run = async (fn: () => Promise<any>) => {
    setBusy(true); setError("");
    try { await fn(); setNote(""); setFile(null); await refresh(); }
    catch (e) { setError(friendlyError(e)); }
    finally { setBusy(false); }
  };

  const upload = (stage: "BEFORE" | "AFTER") => {
    if (!file || note.trim().length < 3) {
      setError("Choose a photo and explain what it establishes before uploading."); return;
    }
    run(() => api.uploadTicketEvidence(ticket.id, stage, file, note, role));
  };

  return <article className={`resolution-case priority-${ticket.priority?.toLowerCase()}`}>
    <header className="resolution-case-head">
      <div>
        <div className="case-eyebrow"><span>{sourceLabel(ticket)}</span><span>{shortId(ticket.id)}</span></div>
        <h3>{ticket.title}</h3>
      </div>
      <span className={`badge ${ticket.status === "CLOSED_VERIFIED" ? "ok" : ticket.status === "RESOLVED_PENDING_VERIFICATION" ? "signal" : "amber"}`}>
        {humanize(ticket.status)}
      </span>
    </header>

    <div className="canonical-case" role="note">
      <div><span>Assigned owner</span><b>{ticket.assigned_role}</b></div>
      <div><span>Due</span><b>{formatDate(ticket.due_date)}</b></div>
      <div><span>Case source</span><b>{sourceLabel(ticket)}</b></div>
      <div><span>Created</span><b>{formatDate(ticket.created_at)}</b></div>
    </div>

    <p className="case-description">{ticket.description}</p>

    <div className="case-linkage" aria-label="Canonical case lineage">
      <span>Evidence</span><i>→</i>
      <span>{finding ? `Finding · ${shortId(finding.id)}` : refs.observations.length ? `Observation · ${shortId(refs.observations[0])}` : `${refs.reviews.length} review source${refs.reviews.length === 1 ? "" : "s"}`}</span><i>→</i>
      <span>{action ? `Action · ${shortId(action.id)}` : `Ticket · ${shortId(ticket.id)}`}</span>
    </div>
    {(finding || action) && <div className="case-link-detail">
      {finding && <span><b>Finding:</b> {finding.title}</span>}
      {action && <span><b>Corrective action:</b> {action.description}</span>}
      <span><b>Canonical ticket:</b> {ticket.id}</span>
    </div>}

    <EvidenceStrip title="Before" rows={ticket.before_evidence || []} />
    <EvidenceStrip title="After" rows={ticket.after_evidence || []} />

    {canOperate && ticket.status === "PENDING_VALIDATION" && <section className="case-work-step">
      <div><span className="step-number">1</span><div><b>Validate the reported condition</b><p>The original field photo is reused above when available. Add another only if it materially improves the record.</p></div></div>
      <label>What did you confirm on site?
        <textarea value={note} onChange={event => setNote(event.target.value)} placeholder="Example: Standing water remains beside the third-tee path; no barrier is present." />
      </label>
      <label className="case-file">Optional supporting photo<input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
      <div className="case-actions">
        {file && <button className="ghost" disabled={busy} onClick={() => upload("BEFORE")}>Add supporting before photo</button>}
        <button className="primary" disabled={busy || !(ticket.before_evidence || []).length} onClick={() => run(() => api.validateTicket(ticket.id, "VALIDATED_ON_SITE", role, note || "Condition confirmed during on-site inspection"))}>Confirm condition</button>
        <button className="ghost" disabled={busy} onClick={() => run(() => api.validateTicket(ticket.id, "NOT_SUBSTANTIATED", role, note || "Condition was not present during on-site inspection"))}>Not substantiated</button>
      </div>
    </section>}

    {canOperate && ticket.status === "OPEN" && <section className="case-work-step">
      <div><span className="step-number">2</span><div><b>Show the correction</b><p>Upload a new after photo and explain what changed. The Brand Leader verifies independently.</p></div></div>
      <label>Resolution note<textarea value={note} onChange={event => setNote(event.target.value)} placeholder="What was fixed, where, and what does the after photo prove?" /></label>
      <label className="case-file">Corrected-condition photo<input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
      <div className="case-actions">
        <button className="ghost" disabled={busy || !file} onClick={() => upload("AFTER")}>Upload after photo</button>
        <button className="primary" disabled={busy || !(ticket.after_evidence || []).length} onClick={() => run(() => api.resolveTicket(ticket.id, role, note || "Corrective work completed; after evidence attached"))}>Submit for independent verification</button>
      </div>
    </section>}

    {ticket.status === "RESOLVED_PENDING_VERIFICATION" && canOperate && <div className="handoff-banner">
      <b>Submitted · awaiting independent verification</b>
      <span>You cannot verify work you validated or resolved. This case is now in the Brand Leader verification queue.</span>
    </div>}

    {ticket.status === "RESOLVED_PENDING_VERIFICATION" && canVerify && <section className="case-work-step verification-step">
      <div><span className="step-number">3</span><div><b>Independent verification</b><p>Compare the original and after evidence, then record why the correction is acceptable.</p></div></div>
      {!independentActor ? <div className="error-box">This persona participated earlier in the case, so another Brand Leader must verify it.</div> : <>
        <label>Verification note<textarea value={note} onChange={event => setNote(event.target.value)} placeholder="What changed, and why does the evidence support closure?" /></label>
        <button className="primary" disabled={busy || note.trim().length < 3} onClick={() => run(() => api.verifyTicket(ticket.id, role, note))}>Verify and close case</button>
      </>}
    </section>}

    {!canOperate && !canVerify && !terminalStatuses.has(ticket.status) && <div className="handoff-banner neutral-handoff">
      <b>Read-only in this demo persona</b><span>Location Operators validate and resolve. Brand Leaders independently verify. Production identity and permissions come from SSO/RBAC.</span>
    </div>}

    {ticket.status === "CLOSED_VERIFIED" && <div className="handoff-banner closed-handoff">
      <b>Verified closed</b><span>Closure is recorded with before/after evidence and an independent decision trail.</span>
      {!ticket.external_reply?.comment && canOperate && <button className="ghost" disabled={busy} onClick={() => run(() => api.draftTicketReply(ticket.id))}>Draft public owner reply</button>}
    </div>}

    {error && <div className="error-box" role="alert">{error}</div>}
    {ticket.external_reply?.comment && <div className="reply-draft"><b>Public owner reply draft</b> <Prov p="AWAITING_BUSINESS_PROFILE_AUTH" /><p>{ticket.external_reply.comment}</p><div className="notice">{ticket.external_reply.note}</div></div>}

    <details className="case-trail">
      <summary>Decision and evidence trail ({ticket.events?.length || 0})</summary>
      {(ticket.events || []).map((event: any, index: number) => <div key={index} className="trail-row">
        <b>{humanize(event.event)}</b><span>{formatDate(event.at)} · {event.by || "System"}</span>{event.note && <p>{event.note}</p>}
      </div>)}
    </details>
  </article>;
}

function LearningQueue({ proposals, role, refresh }: { proposals: any[]; role: string; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const canGovern = role === "Brand Leader";
  const decide = async (id: string, decision: "APPROVE" | "REJECT") => {
    setBusy(id); setError("");
    try {
      await api.decideTaxonomy(id, decision, role, decision === "APPROVE" ? "Recurring language merits standards-owner design review" : "Existing taxonomy already covers this need");
      await refresh();
    } catch (e) { setError(friendlyError(e)); }
    finally { setBusy(""); }
  };
  return <details className="learning-queue">
    <summary>Governed learning proposals <span className="badge neutral">{proposals.filter(p => p.status === "PENDING_REVIEW").length} pending</span></summary>
    <p className="notice">Recurring language can propose a measurable parameter. Nothing silently retrains a model or rewrites a standard.</p>
    {error && <div className="error-box" role="alert">{error}</div>}
    {proposals.length === 0 && <div className="notice">No candidate parameters yet.</div>}
    {proposals.map(proposal => <div className="panel-block" key={proposal.id}>
      <div className="ticket-head"><div><b>{proposal.label}</b> <span className="mono">{proposal.proposed_key}</span></div><span className={`badge ${proposal.status === "APPROVED_FOR_DESIGN" ? "ok" : "neutral"}`}>{humanize(proposal.status)}</span></div>
      <p>{proposal.rationale}</p><div className="notice">{proposal.example_refs.length} anonymized examples · {proposal.effect}</div>
      {proposal.status === "PENDING_REVIEW" && canGovern && <div className="pill-options"><button className="primary" disabled={busy === proposal.id} onClick={() => decide(proposal.id, "APPROVE")}>Approve for design</button><button className="ghost" disabled={busy === proposal.id} onClick={() => decide(proposal.id, "REJECT")}>Reject</button></div>}
    </div>)}
  </details>;
}

export default function ResolutionHub({ ctx }: { ctx: Ctx }) {
  const [tickets, setTickets] = useState<any[]>([]);
  const [proposals, setProposals] = useState<any[]>([]);
  const [analytics, setAnalytics] = useState<any>(null);
  const [currentAudit, setCurrentAudit] = useState<any>(null);
  const [queueFilter, setQueueFilter] = useState<QueueFilter>(ctx.role === "Brand Leader" ? "VERIFY" : "ACTIVE");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>(ctx.auditId ? "CURRENT_AUDIT" : "ALL");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    const [ticketData, metrics, taxonomy, audit] = await Promise.all([
      api.tickets(ctx.locationId), api.resolutionAnalytics(ctx.locationId), api.taxonomy(ctx.locationId),
      ctx.auditId ? api.getAudit(ctx.auditId).catch(() => null) : Promise.resolve(null),
    ]);
    setTickets(ticketData.tickets); setAnalytics(metrics); setProposals(taxonomy.proposals); setCurrentAudit(audit);
  };

  useEffect(() => { setError(""); refresh().catch(e => setError(friendlyError(e))); }, [ctx.locationId, ctx.auditId]);
  useEffect(() => {
    setQueueFilter(ctx.role === "Brand Leader" ? "VERIFY" : "ACTIVE");
  }, [ctx.role]);

  const auditRefs = useMemo(() => new Set([
    ...(currentAudit?.observations || []).map((row: any) => row.id),
    ...(currentAudit?.findings || []).map((row: any) => row.id),
    ...(currentAudit?.actions || []).map((row: any) => row.id),
  ]), [currentAudit]);

  const sourceMatchedTickets = useMemo(() => tickets.filter(ticket => {
    const isCurrent = (ticket.source_refs || []).some((ref: string) => auditRefs.has(ref));
    return sourceFilter === "ALL" || (sourceFilter === "CURRENT_AUDIT" ? isCurrent : ticket.source_kind === "CUSTOMER_SIGNAL_THEME");
  }), [tickets, sourceFilter, auditRefs]);

  const effectiveQueueFilter: QueueFilter = ctx.role === "Brand Leader" ? "VERIFY" : queueFilter;
  const filtered = useMemo(() => sourceMatchedTickets.filter(ticket => effectiveQueueFilter === "ALL"
    || (effectiveQueueFilter === "ACTIVE" && ["PENDING_VALIDATION", "OPEN"].includes(ticket.status))
    || (effectiveQueueFilter === "VERIFY" && ticket.status === "RESOLVED_PENDING_VERIFICATION")
    || (effectiveQueueFilter === "CLOSED" && terminalStatuses.has(ticket.status))),
  [sourceMatchedTickets, effectiveQueueFilter]);

  const groups = useMemo(() => {
    const result = new Map<string, any[]>();
    filtered.forEach(ticket => {
      const isCurrent = (ticket.source_refs || []).some((ref: string) => auditRefs.has(ref));
      const origin = isCurrent ? `Current audit · ${shortId(ctx.auditId || undefined)}` : ticket.source_kind === "CUSTOMER_SIGNAL_THEME" ? "Customer feedback cases" : "Other field work";
      const key = `${origin} · ${formatDate(ticket.created_at)}`;
      result.set(key, [...(result.get(key) || []), ticket]);
    });
    return [...result.entries()];
  }, [filtered, auditRefs, ctx.auditId]);

  const sync = async () => {
    setBusy(true); setError("");
    try { await api.syncTickets(ctx.locationId); await api.syncTaxonomy(ctx.locationId); await refresh(); }
    catch (e) { setError(friendlyError(e)); }
    finally { setBusy(false); }
  };

  const queueCount = (filter: QueueFilter) => sourceMatchedTickets.filter(ticket => filter === "ALL"
    || (filter === "ACTIVE" && ["PENDING_VALIDATION", "OPEN"].includes(ticket.status))
    || (filter === "VERIFY" && ticket.status === "RESOLVED_PENDING_VERIFICATION")
    || (filter === "CLOSED" && terminalStatuses.has(ticket.status))).length;

  return <div className="resolution-hub">
    <header className="resolution-heading">
      <div><span className="reviewer-kicker">EVIDENCE TO OUTCOME</span><h1>{ctx.role === "Brand Leader" ? "Verification queue" : "My actions"}</h1><p>{ctx.role === "Brand Leader" ? "Independently compare before-and-after evidence before closing a case." : "Validate assigned work, show the correction, then hand it to an independent verifier."}</p></div>
      {ctx.role === "Location Operator" && <button className="ghost" disabled={busy} onClick={sync}>{busy ? "Syncing…" : "Sync customer feedback"}</button>}
    </header>

    <div className="demo-role-truth" role="note"><b>Demo persona: {ctx.role}</b><span>This selector previews role-specific workflow; it is not authentication. Production must bind actor identity and permissions through SSO/RBAC.</span></div>
    {error && <div className="error-box" role="alert">{error}</div>}

    {analytics && <div className="resolution-metrics">
      <div><b>{analytics.tickets.open}</b><span>location-wide open</span></div><div><b>{analytics.tickets.closed_verified}</b><span>location-wide verified</span></div><div><b>{analytics.tickets.mean_time_to_verified_hours ?? "—"}</b><span>mean hours to verify</span></div><div><b>{humanize(analytics.rating_impact.state)}</b><span>rating impact state</span></div>
    </div>}

    <div className="queue-toolbar">
      {ctx.role === "Brand Leader" ? <div className="verification-scope"><b>Awaiting independent verification</b><span>{queueCount("VERIFY")} eligible case{queueCount("VERIFY") === 1 ? "" : "s"}</span></div> : <div className="queue-tabs" aria-label="Ticket status filter">
        {(["ACTIVE", "VERIFY", "CLOSED", "ALL"] as QueueFilter[]).map(filter => <button key={filter} className={queueFilter === filter ? "active" : ""} onClick={() => setQueueFilter(filter)}>{filter === "VERIFY" ? "Awaiting verification" : humanize(filter)} <span>{queueCount(filter)}</span></button>)}
      </div>}
      <label>Case source<select value={sourceFilter} onChange={event => setSourceFilter(event.target.value as SourceFilter)}><option value="ALL">All case sources</option>{ctx.auditId && <option value="CURRENT_AUDIT">Current audit only</option>}<option value="CUSTOMER_SIGNAL">Customer feedback only</option></select></label>
    </div>

    {groups.length === 0 ? <div className="empty-queue"><b>No cases in this view</b><p>{ctx.role === "Brand Leader" && queueFilter === "VERIFY" ? "Nothing is waiting for independent verification. Completed operator work will appear here automatically." : "Change the status or source filter to inspect other cases."}</p></div> : groups.map(([label, rows]) => <section className="case-group" key={label}><h2>{label}<span>{rows.length}</span></h2>{rows.map(ticket => <TicketCard key={ticket.id} ticket={ticket} role={ctx.role} currentAudit={currentAudit} refresh={refresh} />)}</section>)}

    <LearningQueue proposals={proposals} role={ctx.role} refresh={refresh} />
  </div>;
}
