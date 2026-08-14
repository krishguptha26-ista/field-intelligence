import { useEffect, useState } from "react";
import { api } from "./api";
import Portfolio from "./screens/Portfolio";
import Audit from "./screens/Audit";
import Workbench from "./screens/Workbench";
import Signals from "./screens/Signals";
import ConsoleScreen from "./screens/ConsoleScreen";
import EvalLab from "./screens/EvalLab";

export type Ctx = {
  tenants: any[];
  tenantId: string;
  locationId: string;
  setLocation: (t: string, l: string) => void;
  auditId: string | null;
  setAuditId: (id: string | null) => void;
  role: string;
};

const SCREENS = [
  ["portfolio", "Portfolio pulse"],
  ["audit", "Live audit"],
  ["workbench", "Finding workbench"],
  ["signals", "Customer signals"],
  ["console", "Cost & observability"],
  ["evals", "Eval Lab"],
] as const;

export default function App() {
  const [screen, setScreen] = useState<string>("portfolio");
  const [tenants, setTenants] = useState<any[]>([]);
  const [tenantId, setTenantId] = useState("broadpeak-demo");
  const [locationId, setLocationId] = useState("wolf-creek-atlanta");
  const [auditId, setAuditId] = useState<string | null>(null);
  const [role, setRole] = useState("Field Consultant");
  const [health, setHealth] = useState<any>(null);

  useEffect(() => {
    api.tenants().then(setTenants).catch(() => {});
    api.health().then(setHealth).catch(() => {});
  }, []);

  const ctx: Ctx = {
    tenants, tenantId, locationId,
    setLocation: (t, l) => { setTenantId(t); setLocationId(l); setAuditId(null); },
    auditId, setAuditId, role,
  };

  const locName = tenants.flatMap(t => t.locations).find((l: any) => l.id === locationId)?.name ?? locationId;

  return (
    <div className="app">
      <nav className="side">
        <div className="brand"><span className="fi">Field</span> Intelligence</div>
        <div className="brand-sub">FranAi capability · POC</div>
        {SCREENS.map(([id, label]) => (
          <button key={id} className={`navbtn ${screen === id ? "active" : ""}`}
                  onClick={() => setScreen(id)}>{label}</button>
        ))}
        <div className="spacer" />
        <label>Viewing as</label>
        <select value={role} onChange={e => setRole(e.target.value)}>
          <option>Field Consultant</option>
          <option>Location Operator</option>
          <option>Brand Leader</option>
          <option>Reviewer</option>
        </select>
        <div style={{ marginTop: 12, fontSize: 11.5, color: "var(--stone-500)" }}>
          {health && (<>
            <div>LLM: <b style={{ color: health.active_provider === "gemini" ? "#7fd8a8" : "var(--gold-soft)" }}>
              {health.active_provider === "gemini" ? "Gemini live" : "fixture engine"}</b></div>
            <div>Places: <b style={{ color: health.maps_key_present ? "#7fd8a8" : "var(--gold-soft)" }}>
              {health.maps_key_present ? "live key" : "fixture"}</b></div>
          </>)}
        </div>
      </nav>
      <main className="main">
        <div className="topbar">
          <select value={`${tenantId}|${locationId}`}
                  onChange={e => { const [t, l] = e.target.value.split("|"); ctx.setLocation(t, l); }}>
            {tenants.map(t => t.locations.map((l: any) => (
              <option key={l.id} value={`${t.id}|${l.id}`}>{t.name} — {l.name}</option>
            )))}
          </select>
          <span className="notice">Active location: {locName}</span>
        </div>
        {screen === "portfolio" && <Portfolio ctx={ctx} goto={setScreen} />}
        {screen === "audit" && <Audit ctx={ctx} goto={setScreen} />}
        {screen === "workbench" && <Workbench ctx={ctx} />}
        {screen === "signals" && <Signals ctx={ctx} />}
        {screen === "console" && <ConsoleScreen />}
        {screen === "evals" && <EvalLab />}
      </main>
    </div>
  );
}

export function Prov({ p }: { p: string }) {
  const cls = p?.includes("LIVE") ? "live" : p?.includes("CACHED") ? "cached"
    : p?.includes("SIMULATED") ? "amber" : "fixture";
  return <span className={`badge ${cls}`}>{p}</span>;
}
