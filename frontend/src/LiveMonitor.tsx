import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./live.css";

const API = import.meta.env.VITE_API_BASE_URL || "/api";
type RecordValue = Record<string, any>;

const STATUS_COLORS: Record<string, string> = {
  no_qualifying_evidence: "#3fa796",
  confirmation_gap: "#e3a33a",
  evidence_requires_clinician_review: "#d4603b",
  data_quality_review: "#6f8489",
  out_of_scope: "#c7d3d4",
};

async function call(path: string, token: string, method = "GET", body?: RecordValue) {
  const response = await fetch(`${API}${path}`, { method, body: body ? JSON.stringify(body) : undefined, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "Request failed");
  return payload;
}

const pct = (part: number, total: number) => (total ? Math.round((part / total) * 100) : 0);
const simDate = (iso?: string | null) => iso ? new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }) : "—";
const klTime = (date: Date) => `${date.toLocaleTimeString("en-GB", { timeZone: "Asia/Kuala_Lumpur" })} MYT`;

type LiveMonitorProps = { token: string, compact?: boolean, tickSeconds?: number, scenario?: "steady" | "catch_up" | "surge", hideControls?: boolean, onDemo?: () => void };

export default function LiveMonitor({ token, compact = false, tickSeconds, scenario, hideControls = false, onDemo }: LiveMonitorProps) {
  const [data, setData] = useState<RecordValue | null>(null);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [busy, setBusy] = useState(false);
  const [patients, setPatients] = useState<RecordValue[]>([]);
  const [patientId, setPatientId] = useState("");
  const [patient, setPatient] = useState<RecordValue | null>(null);
  const inFlight = useRef(false);

  const running = data?.simulation?.status === "running";

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const [dashboard, detail] = await Promise.all([
        call("/live/dashboard", token),
        patientId && !compact ? call(`/patients/${patientId}`, token) : Promise.resolve(null),
      ]);
      setData(dashboard); setPatient(detail); setUpdatedAt(new Date()); setError("");
    } catch (err: any) { setError(err.message); }
    finally { inFlight.current = false; }
  }, [token, patientId, compact]);

  useEffect(() => { call("/patients", token).then((items) => { setPatients(items); if (items.length && !patientId) setPatientId(items[0].id); }).catch((err) => setError(err.message)); }, [token]);

  useEffect(() => {
    load();
    const every = running ? (compact ? 1500 : 3000) : (compact ? 5000 : 15000);
    const timer = window.setInterval(() => { if (!document.hidden) load(); }, every);
    return () => window.clearInterval(timer);
  }, [load, running, compact]);

  const control = async (action: "start" | "stop" | "step" | "reset") => {
    try { setBusy(true); setError(""); await call(`/live/simulation/${action}`, token, "POST", action === "start" && (tickSeconds || scenario) ? { tick_seconds: tickSeconds, scenario } : undefined); await load(); }
    catch (err: any) { setError(err.message); }
    finally { setBusy(false); }
  };

  if (!data) return <div className="loading">{error || "Loading live monitor…"}</div>;
  const statuses: RecordValue[] = data.statuses;
  const current = data.current;
  const series: RecordValue[] = data.series;
  const previous = series.length > 1 ? series[series.length - 2] : null;
  const review = current.by_status.evidence_requires_clinician_review;
  const gap = current.by_status.confirmation_gap;
  const sim = data.simulation;
  const reviewPct = pct(review, current.total);
  const first = series.length ? series[0] : null;
  const baselinePct = first ? pct(first.by_status.evidence_requires_clinician_review, Object.values(first.by_status as Record<string, number>).reduce((a, b) => a + b, 0)) : reviewPct;
  const alarmAt = Math.max(20, baselinePct + 12);
  const alarm = running || sim.status === "stopped" ? reviewPct >= alarmAt : false;
  const newAlerts = previous ? current.open_alerts - previous.open_alerts : null;

  return <section className={`live${compact ? " compact" : ""}${alarm ? " alarm" : ""}`}>
    <div className="live-bar">
      <div className="live-bar-meta">
        <span className={`live-pill ${running ? "on" : ""}`}><i />{running ? "Simulation running" : sim.status === "stopped" ? "Simulation stopped" : sim.status === "error" ? "Simulation error" : "Idle"}</span>
        <span><b>{data.scope.organization}</b></span>
        <span>Simulated date <b>{simDate(sim.simulated_now)}</b> <small>{data.time_label}</small></span>
        <span>Updated {updatedAt ? klTime(updatedAt) : "—"}</span>
      </div>
      {onDemo && <button className="secondary" onClick={onDemo}>Demo mode ⧉</button>}
      {sim.can_control && !hideControls && <div className="live-controls">
        {!running && <button className="primary" disabled={busy || !sim.available} onClick={() => control("start")}>Start simulation</button>}
        {running && <button className="secondary" disabled={busy} onClick={() => control("stop")}>Stop</button>}
        {running && <button className="secondary" disabled={busy} onClick={() => control("step")}>Advance one step</button>}
        <button className="link-button" disabled={busy} onClick={() => { if (window.confirm("Remove all simulated observations, scans and alerts?")) control("reset"); }}>Reset simulation</button>
      </div>}
    </div>
    {error && <div className="error">{error}</div>}
    {sim.error_summary && <div className="error">Last simulation stopped with an error: {sim.error_summary}</div>}
    {alarm && <div className="live-alarm" role="status"><b>Review workload alert</b> {reviewPct}% of monitored profiles now need clinician review (run baseline {baselinePct}%). Review queue is filling faster than usual.</div>}

    <div className="live-kpis">
      {compact && current.overdue !== null && current.overdue !== undefined
        ? <Kpi label={`Overdue HbA1c (>${current.overdue_days} days)`} value={current.overdue} detail={`of ${current.total} monitored profiles`} delta={previous && previous.overdue !== null ? current.overdue - previous.overdue : null} tone={current.overdue > current.total * 0.2 ? "amber" : undefined} />
        : <Kpi label="Monitored profiles" value={current.total} detail={current.overdue !== null && current.overdue !== undefined ? `${current.overdue} overdue for HbA1c (>${current.overdue_days} days)` : "Synthetic T2DM records in scope"} />}
      <Kpi label="Clinician review required" value={`${reviewPct}%`} detail={`${review} profiles`} delta={previous ? review - previous.by_status.evidence_requires_clinician_review : null} tone="coral" />
      <Kpi label="Confirmation gap" value={`${pct(gap, current.total)}%`} detail={`${gap} profiles`} delta={previous ? gap - previous.by_status.confirmation_gap : null} tone="amber" />
      {compact
        ? <Kpi label="New review alerts" value={newAlerts ?? 0} detail={`${current.open_alerts} open in total`} tone={alarm ? "coral" : undefined} />
        : <Kpi label="Open review alerts" value={current.open_alerts} detail="Awaiting acknowledgement" delta={previous ? current.open_alerts - previous.open_alerts : null} />}
    </div>

    <div className="live-grid">
      {!compact && <article className="live-card live-feed">
        <header><h3>Live data pipeline</h3><p>What each simulated step actually wrote to the database and evaluated — newest first</p></header>
        <PipelineFeed feed={data.feed || []} statuses={statuses} running={running} />
      </article>}
      <article className="live-card">
        <header><h3>Review-state trend</h3><p>Share of monitored profiles in each deterministic CPG review state, per simulated step</p></header>
        <Legend statuses={statuses} />
        <TrendLines series={series} statuses={statuses} />
      </article>
    </div>

    <div className="live-grid">
      {!compact && <article className="live-card">
        <header className="live-card-split"><div><h3>Patient trend</h3><p>Verified HbA1c and FPG results (normalised units)</p></div>
          <select aria-label="Synthetic patient" value={patientId} onChange={(event) => setPatientId(event.target.value)}>{patients.map((item) => <option key={item.id} value={item.id}>{item.reference} · {item.care_setting}</option>)}</select>
        </header>
        {patient ? <>
          <div className="live-patient-status">Current review state: <b style={{ color: STATUS_COLORS[patient.guideline_assessment.status] }}>{statuses.find((s) => s.key === patient.guideline_assessment.status)?.label || patient.guideline_assessment.status}</b></div>
          <TrendChart observations={patient.observations} code="hba1c" label="HbA1c (%)" threshold={6.3} />
          <TrendChart observations={patient.observations} code="fpg" label="Fasting plasma glucose (mmol/L)" threshold={7.0} />
          <p className="live-footnote">Dashed line: illustrative demo rule threshold, not clinical guidance.</p>
        </> : <div className="empty">Select a synthetic profile.</div>}
      </article>}
      <div className="live-stack">
        <article className="live-card">
          <header><h3>By care setting</h3><p>Current review-state mix</p></header>
          <div className="live-settings">{data.by_setting.map((row: RecordValue) => <div key={row.care_setting} className="live-setting">
            <div className="live-setting-head"><span>{row.care_setting}</span><b>{pct(row.by_status.evidence_requires_clinician_review, row.total)}% review</b></div>
            <div className="live-stackbar">{statuses.map((s) => row.by_status[s.key] ? <i key={s.key} title={`${s.label}: ${row.by_status[s.key]}`} style={{ width: `${(row.by_status[s.key] / row.total) * 100}%`, background: STATUS_COLORS[s.key] }} /> : null)}</div>
            <small>{row.total} profiles</small>
          </div>)}</div>
        </article>
        <article className="live-card">
          <header><h3>Newest review alerts</h3><p>Created by the normal Sync → Analyse → Route scan</p></header>
          <div className="live-alerts">{data.recent_alerts.length ? data.recent_alerts.map((alert: RecordValue) => <button key={alert.id} className="live-alert" onClick={() => alert.patient && setPatientId(alert.patient.id)}>
            <span className="live-dot" style={{ background: STATUS_COLORS[alert.finding?.status] || "#999" }} />
            <span><b>{alert.patient?.reference}</b><small>{alert.patient?.care_setting} · {statuses.find((s) => s.key === alert.finding?.status)?.label || alert.finding?.status}</small></span>
            <span className="status-tag">{alert.status}</span>
          </button>) : <div className="empty">No review alerts yet.</div>}</div>
        </article>
      </div>
    </div>
    {compact ? <p className="live-footnote">Synthetic data — demonstration only. Accelerated simulated time; the monitor never diagnoses, prescribes, triages or contacts patients.</p> : <p className="live-footnote">Synthetic data — demonstration only. Simulated results use accelerated time and pass through the existing deterministic review tree; the monitor never diagnoses, prescribes, triages or contacts patients. {data.integrity_note}</p>}
  </section>;
}

function Kpi({ label, value, detail, delta, tone }: { label: string, value: React.ReactNode, detail: string, delta?: number | null, tone?: string }) {
  return <article className={`live-kpi ${tone || ""}`}><p>{label}</p><strong key={String(value)}>{value}</strong><small>{detail}{delta ? <em className={delta > 0 ? "up" : "down"}>{delta > 0 ? `▲ ${delta}` : `▼ ${Math.abs(delta)}`} since last step</em> : null}</small></article>;
}

function Legend({ statuses }: { statuses: RecordValue[] }) {
  return <div className="live-legend">{statuses.map((s) => <span key={s.key}><i style={{ background: STATUS_COLORS[s.key] }} />{s.label}</span>)}</div>;
}

const LOINC_NAMES: Record<string, string> = { "4548-4": "HbA1c", "14749-6": "fasting glucose" };

function PipelineFeed({ feed, statuses, running }: { feed: RecordValue[], statuses: RecordValue[], running: boolean }) {
  const label = (key: string) => statuses.find((s) => s.key === key)?.label || key;
  if (!feed.length) return <pre className="live-code"><span className="c">{running ? "-- Waiting for the first simulated step…" : "-- Idle. An administrator starts the simulation; each step is streamed here:"}</span>{"\n"}<span className="c">{"--   INSERT observations → SCAN sync → analyse → route → STATE changes"}</span></pre>;
  return <pre className="live-code" aria-live="polite">{feed.map((step) => <div key={step.step} className="live-code-step">
    <span className="c">{`-- step ${step.step} · simulated ${simDate(step.simulated_from)} → ${simDate(step.simulated_to)}`}</span>{"\n"}
    <span className="k">INSERT INTO</span> observations (patient, loinc, value, unit, observed_at){"\n"}
    {step.observations.map((row: RecordValue, i: number) => <span key={row.source_id}>
      {i ? "     , " : "VALUES "}(<span className="s">'{row.patient}'</span>, <span className="s">'{row.loinc}'</span>, <span className="n">{Number(row.value).toFixed(1)}</span>, <span className="s">'{row.unit}'</span>, <span className="s">'{row.observed_at}'</span>)  <span className="c">{`-- ${LOINC_NAMES[row.loinc] || row.code}`}</span>{"\n"}
    </span>)}
    <span className="c">{step.observations_total > step.observations.length ? `     -- … ${step.observations_total - step.observations.length} more rows; ${step.observations_total} results written in this step` : `     -- ${step.observations_total} results written in this step`}</span>{"\n"}
    <span className="k">SCAN</span> {(step.scan?.stages || []).map((stage: RecordValue, i: number) => <span key={stage.stage}>{i ? " → " : ""}<span className="f">{stage.stage}</span>({stage.stage === "sync" ? <span className="n">{stage.records_received}</span> : stage.stage === "analyse" ? <span className="n">{stage.patients_evaluated}</span> : <>+<span className="n">{stage.new_alerts}</span> alerts</>})</span>)}{"\n"}
    {step.transitions.map((row: RecordValue) => <span key={row.patient}>
      <span className="k">STATE</span> <span className="s">{row.patient}</span>  <span style={{ color: STATUS_COLORS[row.from] }}>{label(row.from)}</span> → <span style={{ color: STATUS_COLORS[row.to] }}>{label(row.to)}</span>{"\n"}
    </span>)}
    {step.transitions_total > step.transitions.length && <><span className="c">{`-- … ${step.transitions_total - step.transitions.length} more state changes (${step.transitions_total} total)`}</span>{"\n"}</>}
    {!step.transitions_total && <><span className="c">-- no review-state changes in this step</span>{"\n"}</>}
  </div>)}</pre>;
}

function TrendLines({ series, statuses }: { series: RecordValue[], statuses: RecordValue[] }) {
  const W = 640, H = 330, L = 34, R = 44, T = 10, B = 26;
  if (series.length < 2) return <div className="empty">The trend appears once a simulation has taken its first step.</div>;
  const shares = series.map((point) => { const total = Object.values(point.by_status as Record<string, number>).reduce((a, b) => a + b, 0) || 1; return Object.fromEntries(statuses.map((s) => [s.key, (point.by_status[s.key] / total) * 100])); });
  const top = Math.min(100, Math.ceil((Math.max(...shares.flatMap((row) => Object.values(row) as number[])) + 5) / 20) * 20);
  const x = (i: number) => L + (i / (series.length - 1)) * (W - L - R);
  const y = (v: number) => T + (1 - v / top) * (H - T - B);
  const grid = [0, top / 4, top / 2, (3 * top) / 4, top];
  const ticks = [...new Set([0, Math.floor((series.length - 1) / 2), series.length - 1])];
  return <svg className="live-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Share of review states over simulated time">
    {grid.map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="grid" /><text x={L - 6} y={y(v) + 3} textAnchor="end">{Math.round(v)}%</text></g>)}
    {statuses.map((s) => <g key={s.key}>
      <polyline points={shares.map((row, i) => `${x(i)},${y(row[s.key])}`).join(" ")} fill="none" stroke={STATUS_COLORS[s.key]} strokeWidth={s.key === "evidence_requires_clinician_review" ? 2.6 : 1.8} strokeLinejoin="round" />
      <text x={W - R + 4} y={y(shares[shares.length - 1][s.key]) + 3} style={{ fill: STATUS_COLORS[s.key] }} className="end-label">{Math.round(shares[shares.length - 1][s.key])}%</text>
    </g>)}
    {ticks.map((i) => <text key={i} x={x(i)} y={H - 6} textAnchor={i === 0 ? "start" : i === series.length - 1 ? "end" : "middle"}>{simDate(series[i].simulated_at)}</text>)}
  </svg>;
}

function TrendChart({ observations, code, label, threshold }: { observations: RecordValue[], code: string, label: string, threshold: number }) {
  const points = useMemo(() => observations
    .filter((o) => o.code === code && o.terminology_status === "verified" && (o.normalised_value ?? null) !== null)
    .map((o) => ({ t: new Date(o.observed_at).getTime(), v: o.normalised_value as number, simulated: String(o.source_id).startsWith("SIM-") }))
    .sort((a, b) => a.t - b.t), [observations, code]);
  const W = 640, H = 120, L = 34, R = 10, T = 10, B = 20;
  if (!points.length) return <div className="live-trend-empty">{label}: no verified results.</div>;
  const values = points.map((p) => p.v).concat(threshold);
  const lo = Math.floor(Math.min(...values) - 0.5), hi = Math.ceil(Math.max(...values) + 0.5);
  const t0 = points[0].t, t1 = points[points.length - 1].t || t0 + 1;
  const x = (t: number) => L + (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const last = points[points.length - 1];
  return <div className="live-trend"><div className="live-trend-head"><span>{label}</span><b>{last.v}</b></div>
    <svg className="live-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label} trend`}>
      {[lo, (lo + hi) / 2, hi].map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="grid" /><text x={L - 6} y={y(v) + 3} textAnchor="end">{Number(v.toFixed(1))}</text></g>)}
      <line x1={L} x2={W - R} y1={y(threshold)} y2={y(threshold)} className="threshold" />
      <polyline points={points.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")} className="trend-line" />
      {points.map((p, i) => <circle key={i} cx={x(p.t)} cy={y(p.v)} r={3} className={p.v >= threshold ? "pt high" : "pt"}><title>{`${simDate(new Date(p.t).toISOString())}: ${p.v}${p.simulated ? " (simulated)" : ""}`}</title></circle>)}
      <text x={L} y={H - 4}>{simDate(new Date(t0).toISOString())}</text><text x={W - R} y={H - 4} textAnchor="end">{simDate(new Date(t1).toISOString())}</text>
    </svg></div>;
}
