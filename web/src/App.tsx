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

function LoginScreen({ onSignedIn }: { onSignedIn: (username: string) => void }) {
  const [username, setUsername] = useState("demo-user");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const session = await api.login(username, password);
      onSignedIn(session.username);
    } catch (err: any) {
      setError(err.message || "Sign-in failed");
    } finally { setBusy(false); }
  };
  return <main className="login-shell">
    <section className="login-card" aria-labelledby="login-title">
      <div className="login-brand"><span className="brand-mark">FI</span><span><b>Field</b> Intelligence</span></div>
      <span className="login-kicker">CONTROLLED ASSESSMENT DEMO</span>
      <h1 id="login-title">Sign in to the workspace</h1>
      <p>Use the shared demo credential provided with this assessment. Do not upload personal or confidential material.</p>
      <form onSubmit={submit}>
        <label>Username<input autoComplete="username" value={username}
          onChange={event => setUsername(event.target.value)} /></label>
        <label>Password<input type="password" autoComplete="current-password" value={password}
          onChange={event => setPassword(event.target.value)} autoFocus /></label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button type="submit" disabled={busy || !username.trim() || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <small>Seeded demonstration data · session expires automatically</small>
    </section>
  </main>;
}

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
  const [authUser, setAuthUser] = useState<string | null | undefined>(undefined);
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
    api.session().then(session => setAuthUser(session.username)).catch(() => setAuthUser(null));
    api.health().then(setHealth).catch(() => {});
  }, []);

  useEffect(() => {
    if (!authUser) return;
    api.tenants().then(setTenants).catch(() => {});
  }, [authUser]);

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

  if (authUser === undefined) return <main className="login-shell"><div className="login-loading">Opening secure demo…</div></main>;
  if (authUser === null) return <LoginScreen onSignedIn={setAuthUser} />;

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
        <button className="signout" onClick={async () => {
          await api.logout().catch(() => {});
          setAuthUser(null);
        }}>Sign out</button>
        {role === "Technical Evaluator" && <div className="provider-status">
          {health && (<>
            <div>LLM: <b style={{ color: health.degraded ? "var(--amber)" : health.active_provider === "gemini" ? "#7fd8a8" : "var(--gold-soft)" }}>
              {health.degraded ? "degraded / fixture fallback" :
                health.readiness === "LIVE_CALL_CONFIRMED" ? "Gemini live call confirmed" :
                health.active_provider === "gemini" ? "Gemini configured / not yet probed" : "fixture engine"}</b></div>
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
