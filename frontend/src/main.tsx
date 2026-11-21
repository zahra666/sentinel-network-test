import React, { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import {
  Activity, Bot, Check, ClipboardCheck, Inbox, LayoutDashboard, Link2, LogOut, Mail, Map as MapIcon, Play, Search,
  Send, Server, ShieldCheck, Users, type LucideIcon,
} from "lucide-react";
import goolaIcon from "./assets/goola-icon.png";
import goolaLoop from "./assets/goola-loop.mp4";
import LiveMonitor from "./LiveMonitor";
import DemoStage from "./DemoStage";
import OutreachPage, { PatientOutreach } from "./Outreach";
import { RenalProfile } from "./RenalProfile";
import { CohortProfile, TherapyList, TreatmentChip, TreatmentSummary } from "./Treatment";
import { Badge, Card, CountUp, Empty, Ring, Sparkline, STATUS_COLORS, Stat, human, toneFor } from "./ui";
import "./styles.css";

const API = import.meta.env.VITE_API_BASE_URL || "/api";
type RecordValue = Record<string, any>;
type PatientTab = "renal" | "outreach" | "therapy";
const PATIENT_TABS: { id: PatientTab, label: string }[] = [
  { id: "renal", label: "Renal profile" },
  { id: "outreach", label: "Outreach" },
  { id: "therapy", label: "Glucose-lowering therapy" },
];

async function api(path: string, token?: string, options: RequestInit = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Request failed");
  return body;
}

type NavItem = { id: string, label: string, icon: LucideIcon, group: string };
const nav: NavItem[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard, group: "Clinical" },
  { id: "live", label: "Live Monitor", icon: Activity, group: "Clinical" },
  { id: "patients", label: "Patients", icon: Users, group: "Clinical" },
  { id: "alerts", label: "Review Queue", icon: Inbox, group: "Clinical" },
  { id: "guideline", label: "Guideline Review", icon: ClipboardCheck, group: "Clinical" },
  { id: "agent", label: "Goola Agent", icon: Bot, group: "Agent" },
  { id: "outreach", label: "Patient Outreach", icon: Mail, group: "Agent" },
  { id: "regional", label: "Regional Surveillance", icon: MapIcon, group: "Research" },
  { id: "ledger", label: "Integrity Ledger", icon: Link2, group: "Research" },
  { id: "governance", label: "AI Governance", icon: ShieldCheck, group: "Administration" },
  { id: "status", label: "System Status", icon: Server, group: "Administration" },
];

const roleLabel: Record<string, string> = { administrator: "Administrator", clinician: "Clinician", researcher: "Researcher", caio: "Governance reviewer" };

function App() {
  const [token, setToken] = useState(localStorage.getItem("sentinel-token") || "");
  const [user, setUser] = useState<RecordValue | null>(null);
  const [page, setPage] = useState("overview");
  const [overview, setOverview] = useState<RecordValue | null>(null);
  const [patients, setPatients] = useState<RecordValue[]>([]);
  const [alerts, setAlerts] = useState<RecordValue[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [demo, setDemo] = useState(() => new URLSearchParams(window.location.search).has("demo"));

  const refresh = async () => {
    if (!token) return;
    try {
      setLoading(true); setError("");
      const me = await api("/auth/me", token);
      setUser(me);
      if (me.role === "caio") {
        setOverview(null);
        if (!["governance", "status"].includes(page)) setPage("governance");
        return;
      }
      if (me.role === "researcher") {
        setOverview(null);
        if (!["regional", "ledger", "status"].includes(page)) setPage("regional");
        return;
      }
      setOverview(await api("/overview", token));
      if (["patients", "guideline"].includes(page)) setPatients(await api("/patients", token));
      if (page === "alerts") setAlerts(await api("/alerts", token));
    } catch (err: any) { setError(err.message); if (String(err.message).includes("token")) setToken(""); }
    finally { setLoading(false); }
  };

  useEffect(() => { refresh(); }, [token, page]);
  useEffect(() => { if (token) localStorage.setItem("sentinel-token", token); else localStorage.removeItem("sentinel-token"); }, [token]);

  if (!token) return <Login onLogin={(data) => { setToken(data.access_token); setUser(data.user); }} />;
  const governanceRole = user?.role === "administrator" || user?.role === "caio";
  const visibleNav = nav.filter(({ id }) => {
    if (user?.role === "caio") return ["governance", "status"].includes(id);
    if (user?.role === "researcher") return ["regional", "ledger", "status"].includes(id);
    return id !== "governance" || governanceRole;
  });
  const runScan = async () => { try { setScanning(true); await api("/scans/run", token, { method: "POST" }); await refresh(); } catch (err: any) { setError(err.message); } finally { setScanning(false); } };
  const content = {
    overview: <Overview data={overview} token={token} />,
    live: <LiveMonitor token={token} onDemo={() => setDemo(true)} />,
    patients: <Patients patients={patients} token={token} />,
    alerts: <Alerts alerts={alerts} token={token} refresh={refresh} />,
    guideline: <GuidelineReview patients={patients} token={token} />,
    agent: <AgentConsole token={token} />,
    outreach: <OutreachPage token={token} />,
    regional: <Regional token={token} />,
    ledger: <Ledger token={token} isAdministrator={user?.role === "administrator"} />,
    governance: governanceRole ? <Governance token={token} /> : <Empty title="Not available for this role" />,
    status: <Status token={token} />,
  }[page];
  const actions = page === "overview" && overview ? <button className="primary" disabled={scanning} onClick={runScan}><Play size={15} />{scanning ? "Running scan…" : "Run scan"}</button> : null;
  const canDemo = user?.role === "administrator" || user?.role === "clinician";
  if (demo && canDemo) return <DemoStage token={token} onExit={() => { setDemo(false); setPage("live"); if (window.location.search) window.history.replaceState(null, "", window.location.pathname); }} />;
  const groups = [...new Set(visibleNav.map((item) => item.group))];
  const current = nav.find((item) => item.id === page);
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><img className="brand-mark brand-logo" src={goolaIcon} alt="" /><div><b>Goola</b><span>Diabetes care intelligence</span></div></div>
      <nav>{groups.map((group) => <div className="nav-group" key={group}><p>{group}</p>{visibleNav.filter((item) => item.group === group).map(({ id, label, icon: Icon }) =>
        <button key={id} className={page === id ? "nav active" : "nav"} onClick={() => setPage(id)} aria-current={page === id ? "page" : undefined}><Icon size={17} strokeWidth={1.9} />{label}</button>)}</div>)}</nav>
    </aside>
    <main className="main">
      <header className="topbar">
        <div className="page-title"><span className="kicker">{current?.group}</span><h1>{current?.label}</h1></div>
        <div className="topbar-right">
          {actions}
          <span className="env-badge">Synthetic data</span>
          <div className="user-chip"><div className="avatar">{(user?.email || "U")[0].toUpperCase()}</div><div><b>{user?.email?.split("@")[0]?.replace(".", " ") || "User"}</b><span>{roleLabel[user?.role] || "Authorised user"}</span></div></div>
          <button className="icon-button" title="Sign out" aria-label="Sign out" onClick={() => setToken("")}><LogOut size={17} /></button>
        </div>
        {loading && <span className="progress" />}
      </header>
      <div className="content">{error && <div className="error">{error}</div>}{content}<p className="safety-note">{SAFETY_NOTE}</p></div>
    </main>
  </div>;
}

function Login({ onLogin }: { onLogin: (data: RecordValue) => void }) {
  const [email, setEmail] = useState("admin@sentinel.demo"); const [password, setPassword] = useState("12345"); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => { event.preventDefault(); try { setBusy(true); setError(""); onLogin(await api("/auth/login", undefined, { method: "POST", body: JSON.stringify({ email, password }) })); } catch (err: any) { setError(err.message); } finally { setBusy(false); } };
  return <div className="login-shell">
    <section className="login-aside">
      <div className="brand"><img className="brand-mark brand-logo" src={goolaIcon} alt="" /><div><b>Goola</b><span>Diabetes care intelligence</span></div></div>
      <div className="login-orbit" aria-hidden="true"><i /><i /><i /><b />{["HbA1c", "eGFR", "UACR", "Fundus exam", "Foot exam"].map((label, index) => <span key={label} className={`signal s${index}`}>{label}</span>)}</div>
      <div className="login-statement"><h2>Every result reviewed.<br /><em>Every complication caught early.</em></h2></div>
      <span className="env-badge" title={SAFETY_NOTE}>Synthetic data — demonstration only</span>
    </section>
    <section className="login-main"><form className="login-card" onSubmit={submit}>
      <h1>Sign in</h1>
      <p>Use your Goola workspace account.</p>
      <label>Email<input value={email} autoComplete="username" onChange={(event) => setEmail(event.target.value)} /></label>
      <label>Password<input type="password" value={password} autoComplete="current-password" onChange={(event) => setPassword(event.target.value)} /></label>
      {error && <div className="error">{error}</div>}
      <button className="primary wide" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form></section>
  </div>;
}

const pct = (part: number, total: number) => (total ? Math.round((part / total) * 100) : 0);

function Overview({ data, token }: { data: RecordValue | null, token: string }) {
  const [live, setLive] = useState<RecordValue | null>(null);
  useEffect(() => { if (data) api("/live/dashboard?points=2", token).then(setLive).catch(() => setLive(null)); }, [token, data]);
  if (!data) return <div className="skeleton-grid"><i /><i /><i /><i /></div>;
  const metrics = data.metrics || {}; const pipeline: RecordValue[] = data.pipeline || []; const recentAlerts: RecordValue[] = data.recent_alerts || [];
  const byStatus: Record<string, number> = live?.current?.by_status || {};
  const total: number = live?.current?.total || metrics.monitored_profiles || 0;
  const review = byStatus.evidence_requires_clinician_review || 0;
  const statuses: RecordValue[] = live?.statuses || [];
  const stageCount = (item: RecordValue) => item.records_received !== undefined ? `${item.records_received.toLocaleString()} records` : item.patients_evaluated !== undefined ? `${item.patients_evaluated.toLocaleString()} patients` : item.new_alerts !== undefined ? `${item.new_alerts} new alerts` : "";
  return <>
    <section className="hero card">
      <div className="hero-ring"><Ring value={pct(review, total)} size={236} stroke={16}><strong><CountUp value={pct(review, total)} suffix="%" /></strong><span>need review</span></Ring></div>
      <div className="hero-copy">
        <span className="kicker">Cohort status</span>
        <h2><CountUp value={review} /> of {total.toLocaleString()} patients need a clinician today.</h2>
        <div className="breakdown">{statuses.map((status) => { const count = byStatus[status.key] || 0; return <div className="breakdown-row" key={status.key}>
          <span className="breakdown-label"><i style={{ background: STATUS_COLORS[status.key] }} />{status.label}</span>
          <span className="breakdown-bar"><b style={{ width: `${pct(count, total)}%`, background: STATUS_COLORS[status.key] }} /></span>
          <span className="breakdown-value">{count.toLocaleString()}</span>
        </div>; })}</div>
      </div>
    </section>
    <section className="stat-grid">
      <Stat label="Monitored profiles" value={metrics.monitored_profiles ?? 0} icon={Users} />
      <Stat label="Open review alerts" value={metrics.open_review_alerts ?? 0} icon={Inbox} tone={metrics.open_review_alerts ? "warning" : undefined} />
      <Stat label="Acknowledged" value={metrics.acknowledged_alerts ?? 0} icon={Check} />
      <Stat label="Source integrity" value={<Badge tone={toneFor(metrics.integrity)}>{human(metrics.integrity || "pending")}</Badge>} icon={Link2} />
    </section>
    <section className="grid-2">
      <Card title="Scan pipeline">
        <ol className="stepper">{pipeline.map((item, index) => <li key={item.stage} className={item.state === "completed" ? "done" : ""}>
          <span className="step-index">{item.state === "completed" ? <Check size={14} strokeWidth={2.5} /> : index + 1}</span>
          <div><b>{human(item.stage)}</b><span>{stageCount(item) || human(item.state)}</span></div>
          <Badge tone={toneFor(item.state)}>{human(item.state)}</Badge>
        </li>)}</ol>
      </Card>
      <Card title="Recent alerts" flush>
        {recentAlerts.length ? <table className="table"><thead><tr><th>Patient</th><th>Finding</th><th>Status</th></tr></thead><tbody>{recentAlerts.map((alert) =>
          <tr key={alert.id}><td className="strong">{alert.patient?.reference}</td><td><span className="dot-label"><i style={{ background: STATUS_COLORS[alert.finding?.status] }} />{human(alert.finding?.status)}</span></td><td><Badge tone={toneFor(alert.status)}>{human(alert.status)}</Badge></td></tr>)}</tbody></table>
          : <Empty title="No alerts yet" text="Run a scan to evaluate the latest results." />}
      </Card>
    </section>
  </>;
}

function Patients({ patients, token }: { patients: RecordValue[], token: string }) {
  const [selected, setSelected] = useState<RecordValue | null>(null); const [detail, setDetail] = useState<RecordValue | null>(null); const [query, setQuery] = useState("");
  const [renal, setRenal] = useState<RecordValue | null>(null);
  const [therapy, setTherapy] = useState<RecordValue | null>(null);
  const [cohort, setCohort] = useState<RecordValue | null>(null);
  const [activeTab, setActiveTab] = useState<PatientTab>("renal");
  const [patientError, setPatientError] = useState("");
  const patientRequest = useRef(0);
  useEffect(() => { api("/treatment/summary", token).then(setTherapy).catch(() => setTherapy(null)); api("/cohort/summary", token).then(setCohort).catch(() => setCohort(null)); }, [token, patients.length]);
  const choose = useCallback(async (patient: RecordValue) => {
    const request = ++patientRequest.current;
    setSelected(patient); setDetail(null); setRenal(null); setPatientError("");
    try {
      const [record, profile] = await Promise.all([api(`/patients/${patient.id}`, token), api(`/patients/${patient.id}/renal-profile`, token).catch(() => null)]);
      if (request !== patientRequest.current) return;
      setDetail(record); setRenal(profile);
    } catch (err: any) { if (request === patientRequest.current) setPatientError(err.message); }
  }, [token]);
  useEffect(() => {
    if (!selected && patients.length) void choose(patients.find((patient) => patient.reference === "SYN-DM-003") || patients[0]);
  }, [choose, patients, selected]);
  const shown = useMemo(() => { const q = query.trim().toLowerCase(); return q ? patients.filter((p) => `${p.reference} ${p.care_setting} ${p.organization}`.toLowerCase().includes(q)) : patients; }, [patients, query]);
  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = PATIENT_TABS.findIndex((tab) => tab.id === activeTab);
    const nextIndex = event.key === "ArrowRight" ? (currentIndex + 1) % PATIENT_TABS.length
      : event.key === "ArrowLeft" ? (currentIndex + PATIENT_TABS.length - 1) % PATIENT_TABS.length
        : event.key === "Home" ? 0 : event.key === "End" ? PATIENT_TABS.length - 1 : currentIndex;
    if (nextIndex === currentIndex) return;
    event.preventDefault();
    const nextTab = PATIENT_TABS[nextIndex].id;
    setActiveTab(nextTab);
    requestAnimationFrame(() => document.getElementById(`patients-tab-${nextTab}`)?.focus());
  };
  return <div className="patients-page">
  <section className="master-detail patients-master-detail">
    <Card title="Patients" count={patients.length} flush className="patients-primary patients-roster" actions={<label className="search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search" aria-label="Search patients" /></label>}>
      <div className="table-scroll"><table className="table selectable with-treatment"><thead><tr><th>Patient</th><th>Care setting</th><th>Age</th><th>Treatment</th><th>Scope</th></tr></thead><tbody>
        {shown.map((patient) => <tr key={patient.id} className={selected?.id === patient.id ? "selected" : ""} onClick={() => choose(patient)}>
          <td><b>{patient.reference}</b><small>{patient.organization}</small></td><td>{patient.care_setting}</td><td className="num">{patient.age_group}{patient.sex && <small title={patient.sex === "female" ? "Female" : "Male"}>{patient.sex === "female" ? "F" : "M"}</small>}</td><td><TreatmentChip treatment={patient.treatment} /></td>
          <td><Badge tone={patient.pregnancy_related ? "warning" : "neutral"}>{patient.pregnancy_related ? "Scope check" : "In scope"}</Badge></td></tr>)}
      </tbody></table></div>
    </Card>
    <Card title={selected?.reference || "Patient record"} subtitle={detail ? `${detail.care_setting} · ${detail.organization || ""}${detail.sex ? ` · ${detail.sex === "female" ? "Female" : "Male"}, ${detail.age_group}` : ""}${detail.body_mass_index ? ` · BMI ${detail.body_mass_index}` : ""}` : "Authorised patient detail"} actions={detail ? <Badge tone={toneFor(detail.guideline_assessment.status)}>{human(detail.guideline_assessment.status)}</Badge> : undefined} flush className="patients-primary patient-detail-card">
      {detail ? <div className="patient-detail-body">
        <PatientEvidence detail={detail} />
        <div className="patient-tabs">
          <div className="patient-tab-list" role="tablist" aria-label="Patient details">
            {PATIENT_TABS.map((tab) => <button key={tab.id} id={`patients-tab-${tab.id}`} type="button" role="tab" aria-selected={activeTab === tab.id} aria-controls={`patients-panel-${tab.id}`} tabIndex={activeTab === tab.id ? 0 : -1} className={activeTab === tab.id ? "patient-tab active" : "patient-tab"} onClick={() => setActiveTab(tab.id)} onKeyDown={handleTabKeyDown}>{tab.label}</button>)}
          </div>
          <div id={`patients-panel-${activeTab}`} className="patient-tab-panel" role="tabpanel" aria-labelledby={`patients-tab-${activeTab}`} tabIndex={0}>
            {activeTab === "renal" ? renal ? <RenalProfile profile={renal} /> : <div className="renal-empty">No renal profile results for this record.</div>
              : activeTab === "outreach" ? <PatientOutreach patientId={detail.id} token={token} />
                : <>
                  <TherapyList treatment={detail.treatment} />
                  {therapy && <div className="card-section">
                    <h4>Cohort therapy summary</h4>
                    <p className="body-text">{therapy.patients} synthetic patients · calibrated to Malaysia NDR 2023</p>
                    <TreatmentSummary summary={therapy} />
                  </div>}
                </>}
          </div>
        </div>
      </div> : selected ? patientError ? <div className="error">{patientError}</div> : <div className="loading-line" /> : <Empty title="Select a patient" text="Choose a profile to view results and review status." />}
    </Card>
  </section>
  {cohort && <section className="patient-support-grid">
    {cohort && <Card title="Cohort profile" subtitle={`${cohort.patients} synthetic patients · sex, hypertension and kidney function`} flush><CohortProfile profile={cohort} /></Card>}
  </section>}
  </div>;
}

const TEST_LABELS: Record<string, string> = { hba1c: "HbA1c", fpg: "Fasting glucose", rpg: "Random glucose", egfr: "eGFR", creatinine: "Creatinine", creatinine_molar: "Creatinine", sodium: "Sodium", potassium: "Potassium", chloride: "Chloride", urea: "Urea", urate: "Uric acid" };

function PatientEvidence({ detail }: { detail: RecordValue }) {
  const hba1c: number[] = detail.observations.filter((o: RecordValue) => o.code === "hba1c" && o.normalised_value != null).sort((x: RecordValue, y: RecordValue) => +new Date(x.observed_at) - +new Date(y.observed_at)).map((o: RecordValue) => o.normalised_value);
  return <div className="patient-case-summary">
    <div className="case-summary-head"><span>Case summary</span>{hba1c.length > 1 && <span className="spark-wrap"><small>HbA1c</small><Sparkline values={hba1c} width={96} height={30} /></span>}</div>
    <table className="table compact"><thead><tr><th>Test</th><th className="num">Result</th><th>Date</th><th>Code</th></tr></thead><tbody>
      {detail.observations.map((observation: RecordValue) => <tr key={observation.id}>
        <td className="strong">{TEST_LABELS[observation.code] || observation.coding?.display || observation.code}{observation.is_corrected && <Badge tone="info">Corrected</Badge>}</td>
        <td className="num">{observation.value ?? "N/A"} <small>{observation.unit}</small></td>
        <td>{new Date(observation.observed_at).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}</td>
        <td>{observation.terminology_status !== "verified" ? <Badge tone="warning">{human(observation.terminology_status)}</Badge> : <code>{observation.coding?.code || observation.original_code || observation.code}</code>}</td>
      </tr>)}
    </tbody></table>
    {detail.conditions?.length ? <div className="card-section"><h4>Conditions</h4>{detail.conditions.map((condition: RecordValue) => <p key={condition.id} className="kv"><span>{condition.snomed?.display || condition.source_coding?.display || "Unmapped condition"}</span><code>{condition.snomed?.code || condition.source_coding?.code}</code></p>)}</div> : null}
    {detail.guideline_assessment.considerations?.[0] && <div className="card-section"><h4>Guideline consideration</h4><p className="body-text">{detail.guideline_assessment.considerations[0]}</p></div>}
  </div>;
}

function Alerts({ alerts, token, refresh }: { alerts: RecordValue[], token: string, refresh: () => Promise<void> }) {
  const act = async (id: string, action: string) => { await api(`/alerts/${id}`, token, { method: "POST", body: JSON.stringify({ action }) }); await refresh(); };
  const open = alerts.filter((alert) => alert.status !== "resolved").length;
  return <Card title="Review queue" count={open} flush>
    {alerts.length ? <table className="table"><thead><tr><th>Patient</th><th>Finding</th><th>Evidence</th><th>Status</th><th /></tr></thead><tbody>
      {alerts.map((alert) => <tr key={alert.id}>
        <td><b>{alert.patient?.reference}</b><small>{alert.patient?.care_setting}</small></td>
        <td>{human(alert.finding?.status)}</td>
        <td className="wrap">{alert.finding?.evidence?.nodes?.find((node: RecordValue) => node.id === "diagnostic-evidence")?.detail}</td>
        <td><Badge tone={toneFor(alert.status)}>{human(alert.status)}</Badge></td>
        <td className="row-actions">{alert.status === "new" && <button className="secondary small" onClick={() => act(alert.id, "acknowledge")}>Acknowledge</button>}{alert.status !== "resolved" && <button className="ghost small" onClick={() => act(alert.id, "resolve")}>Resolve</button>}</td>
      </tr>)}
    </tbody></table> : <Empty title="Queue is clear" text="New alerts appear here after each scan." />}
  </Card>;
}

function GuidelineReview({ patients, token }: { patients: RecordValue[], token: string }) {
  const [patient, setPatient] = useState(""); const [report, setReport] = useState<RecordValue | null>(null); const [selected, setSelected] = useState<string[]>([]); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  const run = async () => { if (!patient) return; try { setBusy(true); const next = await api(`/mdt/${patient}/review`, token, { method: "POST" }); setReport(next); setSelected([]); setMessage(""); } finally { setBusy(false); } };
  const options = report?.report.members[2].considerations.filter((item: RecordValue) => item.pathway !== "clinician_review_required") || [];
  const attest = async () => { if (!report) return; const next = await api(`/mdt/reviews/${report.id}/attest`, token, { method: "POST", body: JSON.stringify({ selected_pathways: selected }) }); setReport(next); setMessage("Attestation recorded. No referral was created."); };
  return <section className="grid-sidebar">
    <Card title="Diabetes MDT review">
      <label className="field">Patient<select value={patient} onChange={(event) => setPatient(event.target.value)}><option value="">Select a patient</option>{patients.map((item) => <option value={item.id} key={item.id}>{item.reference} · {item.care_setting}</option>)}</select></label>
      <button className="primary wide" disabled={!patient || busy} onClick={run}>{busy ? "Generating…" : "Generate review brief"}</button>
    </Card>
    <Card title="Review brief" actions={report ? <Badge tone={report.clinician_attested_at ? "success" : "warning"}>{report.clinician_attested_at ? "Attested" : "Awaiting attestation"}</Badge> : undefined}>
      {report ? <div className="mdt-report">
        {report.report.members.map((member: RecordValue) => <article key={member.member}>
          <h4>{member.member}</h4><p className="body-text">{/^[a-z_]+$/.test(member.finding || "") ? human(member.finding) : member.finding || member.boundary}</p>
          {member.considerations?.map((item: RecordValue) => <div className="consideration" key={item.pathway}><b>{human(item.pathway)}</b><span>{item.rationale}</span></div>)}
        </article>)}
        <div className="card-section flush-x"><h4>Draft review plan</h4><p className="body-text">{report.report.draft_clinician_review_plan.join(" ")}</p></div>
        {options.length > 0 && !report.clinician_attested_at && <div className="attestation"><h4>Clinician attestation</h4>{options.map((item: RecordValue) => <label key={item.pathway} className="check"><input type="checkbox" checked={selected.includes(item.pathway)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, item.pathway] : current.filter((pathway) => pathway !== item.pathway))} />{human(item.pathway)}</label>)}<button className="primary" disabled={!selected.length} onClick={attest}>Record attestation</button></div>}
        {message && <div className="success">{message}</div>}
      </div> : <Empty title="No brief generated" text="Select a patient to generate a structured review brief." />}
    </Card>
  </section>;
}

const AGENT_PROMPTS = ["Email patients with open alerts", "Remind SYN-DM-001", "Verify integrity", "SGLT2 care gaps", "How many patients have high blood pressure?"];
const SAFETY_NOTE = "Synthetic data — demonstration only. Supports clinician review; does not diagnose, prescribe, triage or submit referrals.";

function CareGapTable({ item }: { item: RecordValue }) {
  const rows: RecordValue[] = item.records || [];
  return <div className="care-gap-table">
    {rows.length ? <table className="table compact"><thead><tr><th>Patient</th><th>Hospital</th><th>Criterion met</th><th>Review state</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.patient_reference}><td className="strong">{row.patient_reference}</td><td>{row.organisation}</td><td>{(row.criteria as string[] || []).join(" + ") || "N/A"}</td><td><Badge tone={row.state === "review_candidate" ? "warning" : "neutral"}>{human(row.state)}</Badge></td></tr>)}
    </tbody></table> : <p className="muted-text">No matching records.</p>}
    <small>Clinician review list · {item.rule_version} · not a prescription</small>
  </div>;
}

function AgentConsole({ token }: { token: string }) {
  const [messages, setMessages] = useState<RecordValue[]>([{ role: "assistant", content: "Hello. I can run an evidence review, verify source integrity, prepare a regional analysis, draft patient reminders for your approval, or list T2DM records with ASCVD or eGFR < 60 and no SGLT2-inhibitor entry for clinician review." }]);
  const [text, setText] = useState(""); const [conversation, setConversation] = useState<string | undefined>(); const [busy, setBusy] = useState(false);
  const send = async (event?: FormEvent, preset?: string) => {
    event?.preventDefault(); const outgoing = (preset ?? text).trim(); if (!outgoing) return;
    setText(""); setMessages((items) => [...items, { role: "user", content: outgoing }]); setBusy(true);
    try { const reply = await api("/agent/chat", token, { method: "POST", body: JSON.stringify({ content: outgoing, conversation_id: conversation }) }); setConversation(reply.conversation_id); setMessages((items) => [...items, { role: "assistant", content: reply.message, tool_events: reply.tool_events }]); }
    catch (err: any) { setMessages((items) => [...items, { role: "assistant", content: `I could not complete that request. ${err.message}` }]); }
    finally { setBusy(false); }
  };
  return <section className="agent-layout">
    <Card className="agent-profile">
      <video className="agent-avatar" src={goolaLoop} autoPlay loop muted playsInline aria-label="Goola" />
      <h2>Goola Agent</h2>
      <p className="muted-text">Role-scoped and fully audited.</p>
      <div className="prompt-list"><h4>Suggested</h4>{AGENT_PROMPTS.map((prompt) => <button key={prompt} className="prompt" disabled={busy} onClick={() => send(undefined, prompt)}>{prompt}</button>)}</div>
    </Card>
    <Card className="chat" flush>
      <div className="messages">{messages.map((message, index) => <div key={index} className={`message ${message.role}`}>
        {message.role !== "user" && <img src={goolaIcon} alt="" className="message-avatar" />}
        <div className="bubble"><p>{message.content}</p>{message.tool_events?.length ? <span className="task-chip"><Check size={12} />{message.tool_events.every((item: RecordValue) => item.type === "cohort_count") ? "Counted from records" : `Task queued: ${message.tool_events.map((item: RecordValue) => human(item.type)).join(", ")}`}</span> : null}{message.tool_events?.filter((item: RecordValue) => item.type === "sglt2_care_gap").map((item: RecordValue, eventIndex: number) => <CareGapTable key={eventIndex} item={item} />)}</div>
      </div>)}{busy && <div className="message assistant"><img src={goolaIcon} alt="" className="message-avatar" /><div className="bubble typing"><i /><i /><i /></div></div>}</div>
      <form onSubmit={send}><input value={text} onChange={(event) => setText(event.target.value)} placeholder="Message Goola" /><button className="primary" disabled={busy || !text.trim()} aria-label="Send"><Send size={15} /></button></form>
    </Card>
  </section>;
}

function Regional({ token }: { token: string }) {
  const [data, setData] = useState<RecordValue | null>(null); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const load = async () => { try { setBusy(true); setError(""); setData(await api("/research/regional", token)); } catch (err: any) { setError(err.message); } finally { setBusy(false); } };
  return <Card title="District aggregates" actions={<button className="primary" disabled={busy} onClick={load}>{busy ? "Requesting…" : "Request aggregate"}</button>}>
    {error && <div className="error">{error}</div>}
    {data ? <div className="tile-grid">{data.rows.map((row: RecordValue) => <article className="tile" key={row.district}><span>{row.district}</span><strong>{row.suppressed ? "Suppressed" : `${row.anomaly_rate_among_monitored_patients}%`}</strong><small>{row.eligible_patients} eligible patients</small></article>)}</div>
      : !error && <Empty title="No aggregate requested" text="Aggregates are released only under an active research grant." />}
  </Card>;
}

function Ledger({ token, isAdministrator }: { token: string, isAdministrator: boolean }) {
  const [batches, setBatches] = useState<RecordValue[]>([]); const [result, setResult] = useState<RecordValue | null>(null); const [notice, setNotice] = useState(""); const [error, setError] = useState("");
  const load = async () => { try { setBatches(await api("/ledger", token)); } catch (err: any) { setError(err.message); } };
  useEffect(() => { load(); }, []);
  const verify = async (id: string) => { try { setError(""); setNotice(""); setResult(await api(`/ledger/${id}/verify`, token, { method: "POST" })); } catch (err: any) { setError(err.message); } };
  const adminAction = async (path: string) => { try { setError(""); setResult(null); const response = await api(path, token, { method: "POST" }); setNotice(response.message || human(response.status) || "Action recorded."); await load(); } catch (err: any) { setError(err.message); } };
  return <div className="stack-lg">
    <Card title="Source batches" flush>
      {error && <div className="error inset">{error}</div>}{notice && <div className="success inset">{notice}</div>}
      <table className="table"><thead><tr><th>Batch</th><th>Commitment</th><th>Status</th><th /></tr></thead><tbody>{batches.map((batch) => <tr key={batch.id}>
        <td className="strong">{batch.id.slice(0, 8)}</td><td><code>{batch.commitment.slice(0, 32)}…</code></td><td><Badge tone={toneFor(batch.receipt_status)}>{human(batch.receipt_status)}</Badge></td>
        <td className="row-actions"><button className="secondary small" onClick={() => verify(batch.id)}>Verify</button>{isAdministrator && <><button className="ghost small" onClick={() => adminAction(`/ledger/${batch.id}/anchor`)}>Anchor</button><button className="ghost small" onClick={() => adminAction(`/admin/tamper/${batch.id}`)}>Tamper test</button><button className="ghost small" onClick={() => adminAction(`/admin/restore/${batch.id}`)}>Restore</button></>}</td>
      </tr>)}</tbody></table>
      {result && <div className="card-section"><h4>Verification result</h4><div className="kv-grid">
        <p className="kv"><span>Local commitment</span><Badge tone={result.local_match ? "success" : "danger"}>{result.local_match ? "Match" : "Mismatch"}</Badge></p>
        <p className="kv"><span>On-chain record</span><Badge tone={result.on_chain_match ? "success" : "neutral"}>{result.on_chain_match ? "Match" : human(result.chain_status)}</Badge></p>
        <p className="kv"><span>Recorded</span><code>{String(result.recorded_commitment).slice(0, 24)}…</code></p>
        <p className="kv"><span>Recomputed</span><code>{String(result.local_commitment).slice(0, 24)}…</code></p>
      </div></div>}
    </Card>
    {isAdministrator && <Card title="Controlled test" actions={<button className="secondary" onClick={() => adminAction("/admin/inject-abnormal")}>Inject test observation</button>}><p className="body-text">Adds one labelled abnormal observation for the next scan.</p></Card>}
  </div>;
}

function Governance({ token }: { token: string }) {
  const [data, setData] = useState<RecordValue | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [simulationBusy, setSimulationBusy] = useState(false);
  const load = async () => { try { setError(""); setData(await api("/governance", token)); } catch (err: any) { setError(err.message); } };
  useEffect(() => { load(); }, [token]);
  const recordReview = async (event: FormEvent) => { event.preventDefault(); try { setBusy(true); setError(""); setData(await api("/governance/reviews", token, { method: "POST", body: JSON.stringify({ note: note.trim() || null }) })); setNote(""); } catch (err: any) { setError(err.message); } finally { setBusy(false); } };
  const runMcpSimulation = async () => { try { setSimulationBusy(true); setError(""); const response = await api("/governance/mcp-demo-sessions", token, { method: "POST", body: JSON.stringify({ label: "Synthetic external agent" }) }); setData(response.dashboard); } catch (err: any) { setError(err.message); } finally { setSimulationBusy(false); } };
  const lifecycle: RecordValue[] = data?.lifecycle || [];
  const llm: RecordValue = data?.llm_profile || {};
  const review: RecordValue | null = data?.latest_review || null;
  const mcp: RecordValue = data?.live_evidence?.mcp_activity || {};
  if (error && !data) return <div className="error">{error}</div>;
  if (!data) return <div className="skeleton-grid"><i /><i /><i /><i /></div>;
  return <div className="stack-lg">
    {error && <div className="error">{error}</div>}
    <section className="stat-grid">
      <Stat label="Framework" value={data.framework.primary} hint={data.framework.lifecycle_companion} />
      <Stat label="LLM runtime" value={human(data.live_evidence.llm_runtime.status)} hint="No model connected" />
      <Stat label="Review cycle" value={`${data.review_interval_days} days`} hint={review ? `Next due ${governanceTime(review.next_review_due)}` : "Review required"} />
      <Stat label="External agents" value={mcp.observed_agents_last_five_minutes ?? 0} hint="Active in last 5 minutes" />
    </section>
    <Card title="Lifecycle controls" subtitle="Plan · Do · Check · Act">
      <div className="lifecycle-grid">{lifecycle.map((stage) => <article className="lifecycle-card" key={stage.id}>
        <div className="lifecycle-head"><span className="phase">{stage.phase}</span><Badge tone={toneFor(stage.status)}>{human(stage.status)}</Badge></div>
        <h4>{stage.name}</h4><p>{stage.summary}</p>
        <dl>{stage.evidence.map((item: RecordValue) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>
      </article>)}</div>
    </Card>
    <section className="grid-2">
      <Card title={llm.component_name || "LLM agent profile"} actions={<Badge tone="neutral">{human(llm.integration_status)}</Badge>}>
        <GovernanceList title="Permitted inputs" items={llm.permitted_inputs} />
        <GovernanceList title="Prohibited inputs and actions" items={llm.prohibited_inputs} />
        <GovernanceList title="Required safeguards" items={llm.required_controls} />
        {llm.activation_requirements?.length ? <GovernanceList title="Before activation" items={llm.activation_requirements} /> : null}
      </Card>
      <Card title="Risk and control register">
        <div className="risk-list">{data.risk_controls.map((item: RecordValue) => <article key={item.id}><div className="risk-head"><h4>{item.risk}</h4><Badge tone={toneFor(item.status)}>{human(item.status)}</Badge></div><ul>{item.controls.map((control: string) => <li key={control}>{control}</li>)}</ul></article>)}</div>
      </Card>
    </section>
    <section className="grid-2">
      <Card title="Operational evidence">
        <div className="kv-grid">
          <p className="kv"><span>Latest scan</span><Badge tone={toneFor(data.live_evidence.latest_scan?.status)}>{human(data.live_evidence.latest_scan?.status || "not_run")}</Badge></p>
          <p className="kv"><span>Source integrity</span><Badge tone={toneFor(data.live_evidence.source_batch.receipt_status)}>{human(data.live_evidence.source_batch.receipt_status)}</Badge></p>
          <p className="kv"><span>Scheduler</span><b>{data.live_evidence.scheduler.enabled ? human(data.live_evidence.scheduler.mode || "enabled") : "Manual"}</b></p>
          <p className="kv"><span>Audit events</span><b>{data.live_evidence.recent_audit_events.length}</b></p>
        </div>
        {data.live_evidence.recent_audit_events.length ? <table className="table compact flush-x"><tbody>{data.live_evidence.recent_audit_events.map((item: RecordValue, index: number) => <tr key={`${item.event_type}-${index}`}><td className="strong">{human(item.event_type)}</td><td className="num muted">{governanceTime(item.created_at)}</td></tr>)}</tbody></table> : null}
      </Card>
      <Card title="Governance review" actions={<Badge tone={review?.status === "overdue" ? "warning" : review ? "success" : "neutral"}>{human(review?.status || "required")}</Badge>}>
        {review && <p className="kv"><span>Last review</span><b>{review.reviewed_by} · {governanceTime(review.reviewed_at)}</b></p>}
        {review?.note && <p className="body-text">{review.note}</p>}
        <form onSubmit={recordReview} className="stack-sm"><label className="field">Review note<textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={1000} rows={3} /></label><button className="primary" disabled={busy}>{busy ? "Recording…" : "Record review"}</button></form>
      </Card>
    </section>
    <Card title="External agent access" actions={<button className="secondary" disabled={simulationBusy} onClick={runMcpSimulation}>{simulationBusy ? "Starting…" : "Run 5-minute simulation"}</button>}>
      <div className="kv-grid four">
        <p className="kv"><span>Approved agents, 5 min</span><b>{mcp.observed_agents_last_five_minutes ?? 0}</b></p>
        <p className="kv"><span>Simulated sessions</span><b>{mcp.active_simulated_sessions ?? 0}</b></p>
        <p className="kv"><span>Keys active · revoked · expired</span><b>{mcp.active_keys ?? 0} · {mcp.revoked_keys ?? 0} · {mcp.expired_keys ?? 0}</b></p>
        <p className="kv"><span>Calls allowed · denied, 24 h</span><b>{mcp.allowed_calls_last_24_hours ?? 0} · {mcp.denied_calls_last_24_hours ?? 0}</b></p>
      </div>
    </Card>
    {data.current_gaps?.length ? <Card title="Open gaps"><ul className="list">{data.current_gaps.map((gap: string) => <li key={gap}>{gap}</li>)}</ul></Card> : null}
  </div>;
}
function GovernanceList({ title, items = [] }: { title: string, items?: string[] }) { return <section className="gov-list"><h4>{title}</h4><ul className="list">{items.map((item) => <li key={item}>{item}</li>)}</ul></section>; }
function governanceTime(value?: string) { return value ? new Date(value).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kuala_Lumpur" }) : "N/A"; }

function Status({ token }: { token: string }) {
  const [state, setState] = useState<RecordValue | null>(null);
  useEffect(() => { api("/status", token).then(setState).catch(() => setState(null)); }, []);
  const items: [string, string, LucideIcon][] = [
    ["API", state?.api || "checking", Server],
    ["Goola Agent", state?.sentinel_agent || "checking", Bot],
    ["Scheduler", state ? (state.scheduler?.enabled ? "enabled" : "manual") : "checking", Activity],
    ["Blockchain", state?.chain || "checking", Link2],
  ];
  const agentLabel = (value: string) => value.startsWith("in_app") ? "In-app only" : human(value);
  return <section className="status-grid">{items.map(([label, value, Icon]) => <Card key={label} className="status-card">
    <div className="status-head"><span className="status-icon"><Icon size={18} /></span><span className={`status-dot tone-${toneFor(value)}`} /></div>
    <span className="status-label">{label}</span><strong>{label === "Goola Agent" ? agentLabel(value) : human(value)}</strong>
  </Card>)}</section>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
