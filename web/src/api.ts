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
  signals: (loc: string) => j<any>(`/api/locations/${loc}/signals`),
  createAudit: (tenant_id: string, location_id: string, consultant_name: string) =>
    j<any>("/api/audits", { method: "POST", body: JSON.stringify({ tenant_id, location_id, consultant_name }) }),
  getAudit: (id: string) => j<any>(`/api/audits/${id}`),
  addObservation: (id: string, kind: string, text: string, zone_id?: string | null) =>
    j<any>(`/api/audits/${id}/observations`, { method: "POST", body: JSON.stringify({ kind, text, zone_id }) }),
  analyze: (id: string) => j<any>(`/api/audits/${id}/analyze`, { method: "POST" }),
  answer: (qid: string, answer: string) =>
    j<any>(`/api/questions/${qid}/answer`, { method: "POST", body: JSON.stringify({ answer }) }),
  review: (fid: string, action: string, reviewer: string, reason = "", edits?: any) =>
    j<any>(`/api/findings/${fid}/review`, { method: "POST", body: JSON.stringify({ action, reviewer, reason, edits }) }),
  verifyAction: (aid: string, evidence_description: string) =>
    j<any>(`/api/actions/${aid}/verify`, { method: "POST", body: JSON.stringify({ evidence_description }) }),
  sources: (loc: string) => j<any>(`/api/locations/${loc}/sources`),
  trace: (id: string) => j<any>(`/api/audits/${id}/trace`),
  uploadPhoto: async (id: string, file: File, zone_id?: string | null) => {
    // Multipart: no Content-Type header — the browser must set the boundary.
    const fd = new FormData();
    fd.append("file", file);
    if (zone_id) fd.append("zone_id", zone_id);
    const r = await fetch(`/api/audits/${id}/photo`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
  },
  console: () => j<any>("/api/console"),
  evals: () => j<any>("/api/evals"),
  demoReset: () => j<any>("/api/demo-reset", { method: "POST" }),
};
