import React, { useEffect, useState } from "react";
import { Bean, Check, Eye, Footprints, HeartPulse, type LucideIcon } from "lucide-react";
import { Badge, Card, CountUp, type Tone } from "./ui";
import "./radar.css";

const API = import.meta.env.VITE_API_BASE_URL || "/api";
type RecordValue = Record<string, any>;

async function api(path: string, token: string, options: RequestInit = {}) {
  const response = await fetch(`${API}${path}`, { ...options, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(options.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (HTTP ${response.status})`);
  return body;
}

export const ORGAN_ICON: Record<string, LucideIcon> = { kidney: Bean, eye: Eye, foot: Footprints, heart: HeartPulse };
const STATUS: Record<string, { label: string, tone: Tone }> = {
  needs_review: { label: "Needs review", tone: "danger" },
  overdue: { label: "Overdue", tone: "warning" },
  not_recorded: { label: "Not recorded", tone: "neutral" },
  up_to_date: { label: "Up to date", tone: "success" },
};
const date = (value?: string | null) => value ? new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "Never";

/** Body map: each organ hotspot takes the colour of its screening status. */
function BodyMap({ domains, active, onHover }: { domains: RecordValue[], active: string | null, onHover: (key: string | null) => void }) {
  const status = Object.fromEntries(domains.map((domain) => [domain.key, domain.status]));
  const spots: [string, number, number][] = [["eye", 100, 34], ["heart", 113, 104], ["kidney", 86, 152], ["kidney", 114, 152], ["foot", 82, 318], ["foot", 118, 318]];
  return <svg className="body-map" viewBox="0 0 200 340" role="img" aria-label="Complication screening body map">
    <defs><radialGradient id="body-glow" cx="50%" cy="40%" r="60%"><stop offset="0" stopColor="rgba(255,59,31,.18)" /><stop offset="1" stopColor="rgba(255,59,31,0)" /></radialGradient></defs>
    <ellipse cx="100" cy="150" rx="96" ry="150" fill="url(#body-glow)" />
    <g className="silhouette">
      <circle cx="100" cy="34" r="24" />
      <path d="M88 58h24v10H88z" />
      <path d="M62 74c0-6 5-10 12-10h52c7 0 12 4 12 10l8 82c1 7-4 12-10 12h-4l-2 34H72l-2-34h-4c-6 0-11-5-10-12z" />
      <path d="M56 76c-8 2-12 8-13 16l-9 70c-1 6 8 8 10 2l14-62z" />
      <path d="M144 76c8 2 12 8 13 16l9 70c1 6-8 8-10 2l-14-62z" />
      <path d="M72 200h26l-2 108c0 6-4 10-9 10h-4c-5 0-9-4-9-10z" />
      <path d="M102 200h26l-2 108c0 6-4 10-9 10h-4c-5 0-9-4-9-10z" />
    </g>
    {spots.map(([key, x, y], index) => <g key={index} className={`spot ${status[key] || "not_recorded"}${active === key ? " active" : ""}`} onMouseEnter={() => onHover(key)} onMouseLeave={() => onHover(null)}>
      <circle className="spot-ring" cx={x} cy={y} r="13" />
      <circle className="spot-core" cx={x} cy={y} r="6" />
    </g>)}
  </svg>;
}

function KidneyTrend({ trend }: { trend: RecordValue }) {
  const W = 620, H = 190, L = 40, R = 16, T = 18, B = 30;
  const all = [...trend.points, ...trend.projection.slice(1)];
  const times = all.map((point: RecordValue) => new Date(point.date).getTime());
  const t0 = Math.min(...times), t1 = Math.max(...times);
  const values = [...all.map((point: RecordValue) => point.value), trend.threshold];
  const lo = Math.floor((Math.min(...values) - 4) / 10) * 10, hi = Math.ceil((Math.max(...values) + 4) / 10) * 10;
  const x = (d: string) => L + ((new Date(d).getTime() - t0) / (t1 - t0 || 1)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
  const line = (points: RecordValue[]) => points.map((point) => `${x(point.date).toFixed(1)},${y(point.value).toFixed(1)}`).join(" ");
  const crossing = trend.months_to_threshold != null ? trend.projection.find((point: RecordValue) => point.value <= trend.threshold) : null;
  const ticks = [lo, Math.round((lo + hi) / 2), hi];
  const years = [...new Set(all.map((point: RecordValue) => point.date.slice(0, 4)))] as string[];
  return <div className="kidney-trend">
    <div className="trend-head">
      <div><span className="kicker">Kidney function</span><h4>eGFR trend and projection</h4></div>
      <div className="trend-stats">
        <span><small>Latest</small><b>{trend.latest}</b></span>
        <span><small>Change per year</small><b className={trend.slope_per_year < 0 ? "down" : ""}>{trend.slope_per_year > 0 ? "+" : ""}{trend.slope_per_year}</b></span>
        <span><small>Reaches 60</small><b className={trend.months_to_threshold != null && trend.months_to_threshold <= 24 ? "down" : ""}>{trend.months_to_threshold != null ? <>~<CountUp value={trend.months_to_threshold} /> mo</> : "Not projected"}</b></span>
      </div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} className="trend-chart" role="img" aria-label="eGFR trend with projection">
      {ticks.map((tick) => <g key={tick}><line x1={L} x2={W - R} y1={y(tick)} y2={y(tick)} className="grid" /><text x={L - 8} y={y(tick) + 3} textAnchor="end">{tick}</text></g>)}
      {years.map((year) => { const px = x(`${year}-01-01`); return px >= L && px <= W - R ? <text key={year} x={px} y={H - 8} textAnchor="middle">{year}</text> : null; })}
      <line x1={L} x2={W - R} y1={y(trend.threshold)} y2={y(trend.threshold)} className="threshold" />
      <text x={W - R} y={y(trend.threshold) - 6} textAnchor="end" className="threshold-label">eGFR 60</text>
      {trend.projection.length > 1 && <polyline points={line(trend.projection)} className="projection" />}
      <polyline points={line(trend.points)} className="history" />
      {trend.points.map((point: RecordValue, index: number) => <circle key={index} cx={x(point.date)} cy={y(point.value)} r={index === trend.points.length - 1 ? 5 : 3.5} className={index === trend.points.length - 1 ? "dot latest" : "dot"} />)}
      {crossing && <g><line x1={x(crossing.date)} x2={x(crossing.date)} y1={T} y2={H - B} className="cross-line" /><circle cx={x(crossing.date)} cy={y(trend.threshold)} r="6" className="cross-dot" /></g>}
    </svg>
  </div>;
}

export default function ComplicationRadar({ patientId, token }: { patientId: string, token: string }) {
  const [radar, setRadar] = useState<RecordValue | null>(null);
  const [error, setError] = useState("");
  const [hover, setHover] = useState<string | null>(null);
  const [staged, setStaged] = useState<Record<string, string>>({});
  useEffect(() => { setRadar(null); setStaged({}); api(`/patients/${patientId}/complication-radar`, token).then(setRadar).catch((err) => setError(err.message)); }, [patientId]);
  const stage = async (key: string) => { try { const result = await api(`/patients/${patientId}/complication-radar/${key}/stage`, token, { method: "POST" }); setStaged((current) => ({ ...current, [key]: result.message })); } catch (err: any) { setError(err.message); } };
  if (error && !radar) return <Card title="Complication radar"><div className="error">{error}</div></Card>;
  if (!radar) return <Card title="Complication radar"><div className="loading-line" /></Card>;
  const open = radar.domains.filter((domain: RecordValue) => domain.status !== "up_to_date").length;
  return <Card title="Complication radar" actions={<Badge tone={open ? "danger" : "success"}>{open ? `${open} of 4 need action` : "All screening up to date"}</Badge>} className="radar-card">
    <div className="radar-grid">
      <BodyMap domains={radar.domains} active={hover} onHover={setHover} />
      <div className="organ-grid">{radar.domains.map((domain: RecordValue) => {
        const Icon = ORGAN_ICON[domain.key]; const meta = STATUS[domain.status];
        return <article key={domain.key} className={`organ ${domain.status}${hover === domain.key ? " active" : ""}`} onMouseEnter={() => setHover(domain.key)} onMouseLeave={() => setHover(null)}>
          <header><span className="organ-icon"><Icon size={17} /></span><div><b>{domain.label}</b><small>{domain.condition}</small></div><Badge tone={meta.tone}>{meta.label}</Badge></header>
          <p className="organ-headline">{domain.headline}</p>
          {domain.metrics.length > 0 && <dl>{domain.metrics.map((metric: RecordValue) => <div key={metric.label}><dt>{metric.label}</dt><dd>{metric.value}<small>{metric.unit ? ` ${metric.unit}` : ""}</small></dd></div>)}</dl>}
          <footer><span>Last screened {date(domain.last_screened_at)}</span>
            {domain.action && (staged[domain.key] ? <span className="staged"><Check size={13} />Staged</span> : <button className="secondary small" onClick={() => stage(domain.key)}>{domain.action.label}</button>)}
          </footer>
          {staged[domain.key] && <p className="staged-note">{staged[domain.key]}</p>}
        </article>;
      })}</div>
    </div>
    {radar.kidney_trend && <KidneyTrend trend={radar.kidney_trend} />}
  </Card>;
}

/** Cohort-level view of open screening gaps per complication domain. */
export function CareGapSummary({ token }: { token: string }) {
  const [data, setData] = useState<RecordValue | null>(null);
  useEffect(() => { api("/patients/care-gaps/summary", token).then(setData).catch(() => setData(null)); }, [token]);
  if (!data) return null;
  const labels: Record<string, string> = { kidney: "Kidneys", eye: "Eyes", foot: "Feet and nerves", heart: "Heart" };
  const order = ["needs_review", "overdue", "not_recorded", "up_to_date"];
  return <Card title="Complication screening" subtitle={`${data.patients.toLocaleString()} patients`} className="gap-card">
    <div className="gap-grid">{Object.entries(data.domains).map(([key, counts]: [string, any]) => {
      const Icon = ORGAN_ICON[key]; const gaps = counts.needs_review + counts.overdue + counts.not_recorded;
      return <article key={key} className="gap">
        <header><span className="organ-icon"><Icon size={17} /></span><b>{labels[key]}</b></header>
        <strong><CountUp value={gaps} /></strong><small>open screening gaps</small>
        <div className="gap-bar">{order.map((state) => <i key={state} className={state} style={{ width: `${(counts[state] / (data.patients || 1)) * 100}%` }} title={`${STATUS[state].label}: ${counts[state]}`} />)}</div>
        <div className="gap-legend">{counts.needs_review > 0 && <span className="needs_review">{counts.needs_review} review</span>}{counts.overdue > 0 && <span className="overdue">{counts.overdue} overdue</span>}{counts.not_recorded > 0 && <span className="not_recorded">{counts.not_recorded} missing</span>}</div>
      </article>;
    })}</div>
  </Card>;
}
