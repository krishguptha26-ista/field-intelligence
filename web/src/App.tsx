import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import Portfolio from "./screens/Portfolio";
import Audit from "./screens/Audit";
import Workbench from "./screens/Workbench";
import Signals from "./screens/Signals";
import Benchmark from "./screens/Benchmark";
import ResolutionHub from "./screens/ResolutionHub";
import ConsoleScreen from "./screens/ConsoleScreen";
import EvalLab from "./screens/EvalLab";
import ProductTour from "./components/ProductTour";
import "./app-shell.css";

export type Ctx = {
  tenants: any[];
  tenantId: string;
  locationId: string;
  setLocation: (t: string, l: string) => void;
  auditId: string | null;
  setAuditId: (id: string | null) => void;
  role: string;
};

const SCREENS = {
  portfolio: "Portfolio",
  audit: "Walkthrough",
  workbench: "Review queue",
  signals: "Customer context",
  benchmark: "Competitive insights",
  resolution: "My actions",
  console: "Observability",
  evals: "Eval Lab",
} as const;

type ScreenId = keyof typeof SCREENS;

const ROLE_SCREENS: Record<string, ScreenId[]> = {
  "Field Consultant": ["audit"],
  Reviewer: ["workbench", "signals"],
  "Location Operator": ["resolution", "signals"],
  "Brand Leader": ["portfolio", "resolution", "signals", "benchmark"],
  "Technical Evaluator": [
    "audit", "workbench", "resolution", "portfolio", "signals", "benchmark", "console", "evals",
  ],
};

const DEFAULT_SCREEN: Record<string, ScreenId> = {
  "Field Consultant": "audit",
  Reviewer: "workbench",
  "Location Operator": "resolution",
  "Brand Leader": "portfolio",
  "Technical Evaluator": "audit",
};

export default function App() {
  const initialLocation = localStorage.getItem("fieldintel.location") || "wolf-creek-atlanta";
  const [screen, setScreen] = useState<string>(() => localStorage.getItem("fieldintel.screen") || "audit");
  const [tenants, setTenants] = useState<any[]>([]);
  const [tenantId, setTenantId] = useState(() => localStorage.getItem("fieldintel.tenant") || "broadpeak-demo");
  const [locationId, setLocationId] = useState(initialLocation);
  const [auditId, setAuditIdState] = useState<string | null>(() => localStorage.getItem(`fieldintel.audit.${initialLocation}`));
  const [role, setRole] = useState(() => localStorage.getItem("fieldintel.role") || "Field Consultant");
  const [health, setHealth] = useState<any>(null);
  const [tourOpen, setTourOpen] = useState(() =>
    !localStorage.getItem("fieldintel.tour.seen") &&
    !sessionStorage.getItem("fieldintel.tour.dismissed"));

  useEffect(() => {
    api.tenants().then(setTenants).catch(() => {});
    api.health().then(setHealth).catch(() => {});
  }, []);

  useEffect(() => { localStorage.setItem("fieldintel.screen", screen); }, [screen]);
  useEffect(() => { localStorage.setItem("fieldintel.role", role); }, [role]);

  const visibleScreens = useMemo(
    () => ROLE_SCREENS[role] ?? ROLE_SCREENS["Field Consultant"],
    [role],
  );

  useEffect(() => {
    if (!visibleScreens.includes(screen as ScreenId)) {
      setScreen(DEFAULT_SCREEN[role] ?? "audit");
    }
  }, [role, screen, visibleScreens]);

  const setAuditId = (id: string | null) => {
    setAuditIdState(id);
    const key = `fieldintel.audit.${locationId}`;
    if (id) localStorage.setItem(key, id); else localStorage.removeItem(key);
  };

  const ctx: Ctx = {
    tenants, tenantId, locationId,
    setLocation: (t, l) => {
      setTenantId(t); setLocationId(l);
      localStorage.setItem("fieldintel.tenant", t);
      localStorage.setItem("fieldintel.location", l);
      setAuditIdState(localStorage.getItem(`fieldintel.audit.${l}`));
    },
    auditId, setAuditId, role,
  };

  const locName = tenants.flatMap(t => t.locations).find((l: any) => l.id === locationId)?.name ?? locationId;

  const changeRole = (nextRole: string) => {
    setRole(nextRole);
    setScreen(DEFAULT_SCREEN[nextRole] ?? "audit");
  };

  const personaOptions = (
    <>
      <option>Field Consultant</option>
      <option>Location Operator</option>
      <option>Brand Leader</option>
      <option>Reviewer</option>
      <option>Technical Evaluator</option>
    </>
  );

  const screenLabel = (id: ScreenId) =>
    id === "resolution" && role === "Brand Leader" ? "Verification queue" : SCREENS[id];

  return (
    <div className="app">
      <nav className="side" aria-label="Workspace navigation">
        <div className="brand"><span className="brand-mark">FI</span><span><span className="fi">Field</span> Intelligence</span></div>
        <div className="brand-sub">Walkthrough Copilot</div>
        {visibleScreens.map(id => (
          <button key={id} className={`navbtn ${screen === id ? "active" : ""}`}
                  onClick={() => setScreen(id)}>{screenLabel(id)}</button>
        ))}
        <div className="spacer" />
        <button className="tour-launch" onClick={() => setTourOpen(true)}>How it works</button>
        <div className="persona-switcher">
          <label htmlFor="persona-side">Preview workspace as</label>
          <select id="persona-side" value={role} onChange={e => changeRole(e.target.value)}>{personaOptions}</select>
        </div>
        {role === "Technical Evaluator" && <div className="provider-status">
          {health && (<>
            <div>LLM: <b style={{ color: health.degraded ? "var(--amber)" : health.active_provider === "gemini" ? "#7fd8a8" : "var(--gold-soft)" }}>
              {health.degraded ? "degraded / fixture fallback" : health.active_provider === "gemini" ? "Gemini live" : "fixture engine"}</b></div>
            <div>Places: <b style={{ color: health.maps_key_present ? "#7fd8a8" : "var(--gold-soft)" }}>
              {health.maps_key_present ? "key configured" : "fixture"}</b></div>
            {locationId === "wolf-creek-atlanta" && <div>Reviews: <b style={{ color: "#7fd8a8" }}>362-row snapshot</b></div>}
          </>)}
        </div>}
      </nav>
      <main className="main">
        <div className="topbar">
          <div className="location-context">
            <span>Current location</span>
            <select aria-label="Current location" value={`${tenantId}|${locationId}`}
                    onChange={e => { const [t, l] = e.target.value.split("|"); ctx.setLocation(t, l); }}>
              {tenants.map(t => t.locations.map((l: any) => (
                <option key={l.id} value={`${t.id}|${l.id}`}>{t.name} — {l.name}</option>
              )))}
            </select>
          </div>
          <div className="mobile-persona">
            <span>Workspace</span>
            <select aria-label="Preview workspace as" value={role} onChange={e => changeRole(e.target.value)}>{personaOptions}</select>
          </div>
          <span className="location-name">{locName}</span>
          <button className="tour-launch topbar-tour" onClick={() => setTourOpen(true)}>Help</button>
        </div>
        <div data-tour={screen === "portfolio" ? "portfolio-workspace" : screen === "resolution" ? "resolution-workspace" : screen === "console" ? "technical-workspace" : undefined}>
        {screen === "portfolio" && <Portfolio ctx={ctx} goto={setScreen} />}
        {screen === "audit" && <Audit ctx={ctx} goto={setScreen} />}
        {screen === "workbench" && <div data-tour="review-workspace"><Workbench ctx={ctx} /></div>}
        {screen === "signals" && <Signals ctx={ctx} />}
        {screen === "benchmark" && <Benchmark ctx={ctx} />}
        {screen === "resolution" && <ResolutionHub ctx={ctx} />}
        {screen === "console" && <ConsoleScreen />}
        {screen === "evals" && <EvalLab />}
        </div>
      </main>
      {visibleScreens.length > 1 && <nav className="mobile-nav" aria-label="Workspace navigation">
        {visibleScreens.map(id => (
          <button key={id} className={screen === id ? "active" : ""} onClick={() => setScreen(id)}>
            {screenLabel(id)}
          </button>
        ))}
      </nav>}
      <button className="mobile-tour-launch" aria-label="How it works" onClick={() => setTourOpen(true)}>Tour</button>
      <ProductTour open={tourOpen}
        onClose={(completed) => {
          setTourOpen(false);
          try {
            if (completed) localStorage.setItem("fieldintel.tour.seen", "1");
            else sessionStorage.setItem("fieldintel.tour.dismissed", "1");
          } catch { /* non-persistent browser */ }
        }} />
    </div>
  );
}

export function Prov({ p }: { p: string }) {
  const cls = p?.includes("CACHED") ? "cached" : p?.includes("LIVE") ? "live"
    : p?.includes("SIMULATED") ? "amber" : "fixture";
  return <span className={`badge ${cls}`}>{p}</span>;
}
