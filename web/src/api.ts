const BASE = "";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

export const api = {
  health: () => j<any>("/api/health"),
  simulated: () => j<any>("/api/simulated"),
  tenants: () => j<any[]>("/api/tenants"),
  zones: (loc: string) => j<any[]>(`/api/locations/${loc}/zones`),
  standards: (loc: string) => j<any[]>(`/api/locations/${loc}/standards`),
  fieldGuide: (loc: string) => j<any>(`/api/locations/${loc}/field-guide`),
  signals: (loc: string) => j<any>(`/api/locations/${loc}/signals`),
  benchmark: (loc: string) => j<any>(`/api/locations/${loc}/benchmark`),
  createAudit: (tenant_id: string, location_id: string, consultant_name: string) =>
    j<any>("/api/audits", { method: "POST", body: JSON.stringify({ tenant_id, location_id, consultant_name }) }),
  getAudit: (id: string) => j<any>(`/api/audits/${id}`),
  addObservation: (id: string, kind: string, text: string, zone_id?: string | null) =>
    j<any>(`/api/audits/${id}/observations`, { method: "POST", body: JSON.stringify({ kind, text, zone_id }) }),
  submitChecklist: (id: string, responses: any[]) =>
    j<any>(`/api/audits/${id}/checklist`, {
      method: "POST", body: JSON.stringify({ responses }),
    }),
  analyze: (id: string) => j<any>(`/api/audits/${id}/analyze`, { method: "POST" }),
  auditBudget: (id: string) => j<any>(`/api/audits/${id}/budget`),
  acknowledgeAuditBudget: (id: string, acknowledged_by: string, reason: string, request_id: string) =>
    j<any>(`/api/audits/${id}/budget/acknowledge`, {
      method: "POST", body: JSON.stringify({ acknowledged_by, reason, request_id }),
    }),
  submitAudit: (id: string, submitted_by: string, no_issue_attestation = false) =>
    j<any>(`/api/audits/${id}/submit`, {
      method: "POST", body: JSON.stringify({ submitted_by, no_issue_attestation }),
    }),
  answer: (qid: string, answer: string) =>
    j<any>(`/api/questions/${qid}/answer`, { method: "POST", body: JSON.stringify({ answer }) }),
  confirmObservation: (id: string, text: string) =>
    j<any>(`/api/observations/${id}/confirm`, {
      method: "POST", body: JSON.stringify({ text }),
    }),
  review: (fid: string, action: string, reviewer: string, reason = "", edits?: any) =>
    j<any>(`/api/findings/${fid}/review`, { method: "POST", body: JSON.stringify({ action, reviewer, reason, edits }) }),
  challengeFinding: (fid: string, reviewer: "Reviewer" | "Brand Leader") =>
    j<any>(`/api/findings/${fid}/challenge`, {
      method: "POST", body: JSON.stringify({ reviewer }),
    }),
  verifyAction: (aid: string, evidence_description: string) =>
    j<any>(`/api/actions/${aid}/verify`, { method: "POST", body: JSON.stringify({ evidence_description }) }),
  uploadActionEvidence: async (aid: string, file: File, note: string, actor: string) => {
    const fd = new FormData();
    fd.append("file", file); fd.append("note", note); fd.append("actor", actor);
    const r = await fetch(`/api/actions/${aid}/evidence`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
  },
  sources: (loc: string) => j<any>(`/api/locations/${loc}/sources`),
  tickets: (loc: string) => j<any>(`/api/locations/${loc}/tickets`),
  syncTickets: (loc: string) =>
    j<any>(`/api/locations/${loc}/tickets/sync`, { method: "POST" }),
  taxonomy: (loc: string) => j<any>(`/api/locations/${loc}/taxonomy`),
  syncTaxonomy: (loc: string) =>
    j<any>(`/api/locations/${loc}/taxonomy/sync`, { method: "POST" }),
  decideTaxonomy: (id: string, decision: "APPROVE" | "REJECT", reviewer: string, reason: string) =>
    j<any>(`/api/taxonomy/${id}/decision`, {
      method: "POST", body: JSON.stringify({ decision, reviewer, reason }),
    }),
  resolutionAnalytics: (loc: string) =>
    j<any>(`/api/locations/${loc}/resolution-analytics`),
  validateTicket: (id: string, verdict: string, actor: string, reason: string) =>
    j<any>(`/api/tickets/${id}/validate`, {
      method: "POST", body: JSON.stringify({ verdict, actor, reason }),
    }),
  resolveTicket: (id: string, actor: string, resolution_note: string) =>
    j<any>(`/api/tickets/${id}/resolve`, {
      method: "POST", body: JSON.stringify({ actor, resolution_note }),
    }),
  verifyTicket: (id: string, actor: string, verification_note: string) =>
    j<any>(`/api/tickets/${id}/verify`, {
      method: "POST", body: JSON.stringify({ actor, verification_note }),
    }),
  draftTicketReply: (id: string) =>
    j<any>(`/api/tickets/${id}/reply-draft`, { method: "POST" }),
  uploadTicketEvidence: async (id: string, stage: "BEFORE" | "AFTER", file: File,
                               note: string, actor: string) => {
    const fd = new FormData();
    fd.append("stage", stage); fd.append("file", file);
    fd.append("note", note); fd.append("actor", actor);
    const r = await fetch(`/api/tickets/${id}/evidence`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
  },
  trace: (id: string) => j<any>(`/api/audits/${id}/trace`),
  uploadPhoto: async (id: string, file: File, zone_id?: string | null,
                      privacy_attested = false, supports_observation_id?: string | null,
                      evidence_for_standard_code?: string | null) => {
    // Multipart: no Content-Type header — the browser must set the boundary.
    const fd = new FormData();
    fd.append("file", file);
    if (zone_id) fd.append("zone_id", zone_id);
    if (supports_observation_id) fd.append("supports_observation_id", supports_observation_id);
    if (evidence_for_standard_code) fd.append("evidence_for_standard_code", evidence_for_standard_code);
    fd.append("privacy_attested", String(privacy_attested));
    const r = await fetch(`/api/audits/${id}/photo`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
  },
  uploadMedia: async (id: string, media_kind: "AUDIO" | "VIDEO", file: File,
                      zone_id?: string | null, standard_code?: string | null,
                      privacy_attested = false) => {
    const fd = new FormData();
    fd.append("file", file); fd.append("media_kind", media_kind);
    if (zone_id) fd.append("zone_id", zone_id);
    if (standard_code) fd.append("standard_code", standard_code);
    fd.append("privacy_attested", String(privacy_attested));
    const r = await fetch(`/api/audits/${id}/media`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
  },
  console: () => j<any>("/api/console"),
  evals: () => j<any>("/api/evals"),
  demoReset: () => j<any>("/api/demo-reset", { method: "POST" }),
};
