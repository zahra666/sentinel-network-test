import React, { useEffect, useMemo, useState } from "react";
import { Badge } from "./ui";
import "./renal.css";

type RecordValue = Record<string, any>;

const fmt = (value: number | null | undefined, decimals = 1) => value == null ? "—" : value.toFixed(decimals);
const shortDate = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit", timeZone: "Asia/Kuala_Lumpur" });
const refText = (ref: RecordValue) => (ref.high == null ? `≥ ${ref.low}` : `${ref.low} – ${ref.high}`) + (ref.sex ? ` (${ref.sex === "female" ? "F" : "M"})` : "");

/** Seven-analyte renal profile: report-style latest panel plus one trend chart per analyte. */
export function RenalProfile({ profile }: { profile: RecordValue }) {
  const dates: string[] = profile.panel_dates || [];
  const [date, setDate] = useState<string>(dates[dates.length - 1] || "");
  useEffect(() => setDate(dates[dates.length - 1] || ""), [profile]);
  const analytes: RecordValue[] = profile.analytes || [];
  const flagged = analytes.filter((a) => a.latest?.flag).length;

  if (!dates.length) return <div className="renal-empty">No renal profile results for this record.</div>;
  return <div className="renal">
    <div className="renal-head">
      <div className="renal-dates" role="tablist" aria-label="Panel date">
        {dates.map((d) => <button key={d} role="tab" aria-selected={d === date} className={d === date ? "active" : ""} onClick={() => setDate(d)}>{shortDate(d)}</button>)}
      </div>
      {flagged ? <Badge tone="warning">{flagged} outside reference</Badge> : <Badge tone="success">All within reference</Badge>}
    </div>

    <table className="table compact renal-table">
      <thead><tr><th>Test</th><th className="num">Result</th><th>Reference</th><th>Trend</th></tr></thead>
      <tbody>{analytes.map((a) => {
        const point = a.series.find((p: RecordValue) => p.date === date);
        return <tr key={a.code} className={point?.flag ? `flag-${point.flag}` : ""}>
          <td className="strong">{a.label}<small><code>{a.loinc}</code></small></td>
          <td className="num">{!a.series.length ? <small className="renal-nt">Not tested</small> : <b>{point ? fmt(point.value, a.decimals) : "—"}</b>}{point?.flag && <span className={`renal-flag ${point.flag}`}>{point.flag}</span>}{a.series.length > 0 && <small>{a.unit}</small>}</td>
          <td><small>{refText(a.reference)}</small></td>
          <td><MiniTrend analyte={a} selected={date} /></td>
        </tr>;
      })}</tbody>
    </table>
    {(() => { const egfr = analytes.find((a) => a.code === "egfr"); const point = egfr?.series.find((p: RecordValue) => p.date === date);
      return point?.value != null ? <p className="renal-note">eGFR (CKD-EPI) {fmt(point.value)} · KDIGO category {category(point.value)} for this single value. One result is not a CKD diagnosis; persistence over ≥3 months and albuminuria are needed.</p> : null; })()}

    <div className="renal-grid">{analytes.map((a) => <AnalyteChart key={a.code} analyte={a} selected={date} />)}</div>
    <p className="renal-foot">Synthetic data. {profile.sex ? `Reference intervals for a ${profile.sex} adult. ` : ""}{profile.reference_source} H/L flags mirror a laboratory report and are display aids only.</p>
  </div>;
}

function category(egfr: number) {
  return egfr >= 90 ? "G1" : egfr >= 60 ? "G2" : egfr >= 45 ? "G3a" : egfr >= 30 ? "G3b" : egfr >= 15 ? "G4" : "G5";
}

function usePoints(analyte: RecordValue) {
  return useMemo(() => (analyte.series as RecordValue[]).filter((p) => p.value != null).map((p) => ({ t: new Date(p.observed_at).getTime(), v: p.value as number, flag: p.flag as string | null, date: p.date as string, version: p.version as number })), [analyte]);
}

function scale(points: { t: number, v: number }[], ref: RecordValue) {
  const values = points.map((p) => p.v).concat([ref.low, ref.high].filter((v) => v != null));
  const span = Math.max(...values) - Math.min(...values) || 1;
  return { lo: Math.min(...values) - span * 0.15, hi: Math.max(...values) + span * 0.15 };
}

function MiniTrend({ analyte, selected }: { analyte: RecordValue, selected: string }) {
  const points = usePoints(analyte);
  if (points.length < 2) return <small className="muted">—</small>;
  const W = 72, H = 24, P = 3;
  const { lo, hi } = scale(points, analyte.reference);
  const t0 = points[0].t, t1 = points[points.length - 1].t;
  const x = (t: number) => P + ((t - t0) / (t1 - t0 || 1)) * (W - 2 * P);
  const y = (v: number) => P + (1 - (v - lo) / (hi - lo)) * (H - 2 * P);
  const ref = analyte.reference;
  return <svg className="renal-mini" width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
    <rect x={0} width={W} y={y(ref.high ?? hi)} height={y(ref.low) - y(ref.high ?? hi)} className="band" />
    <polyline points={points.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")} className="line" />
    {points.map((p, i) => <circle key={i} cx={x(p.t)} cy={y(p.v)} r={p.date === selected ? 2.8 : 1.8} className={`pt ${p.flag || ""} ${p.date === selected ? "sel" : ""}`} />)}
  </svg>;
}

function AnalyteChart({ analyte, selected }: { analyte: RecordValue, selected: string }) {
  const points = usePoints(analyte);
  const ref = analyte.reference;
  const latest = analyte.latest;
  const W = 260, H = 118, L = 34, R = 8, T = 8, B = 18;
  const head = <div className="renal-card-head"><span>{analyte.label}</span>
    <b className={latest?.flag ? `f-${latest.flag}` : ""}>{latest ? fmt(latest.value, analyte.decimals) : "—"}{latest?.flag && <em>{latest.flag}</em>}</b>
    <small>{analyte.unit}</small></div>;
  if (!points.length) return <div className="renal-card">{head}<div className="renal-empty">{analyte.series.length ? "No verified results." : analyte.code === "egfr" ? "Not available: creatinine not tested." : "Not tested in this period."}</div></div>;
  const { lo, hi } = scale(points, ref);
  const t0 = points[0].t, t1 = points[points.length - 1].t;
  const x = (t: number) => L + (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const bandTop = y(ref.high ?? hi), bandBottom = y(ref.low ?? lo);
  return <div className="renal-card">{head}
    <svg className="live-chart renal-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${analyte.label} trend with reference interval ${refText(ref)}`}>
      <rect x={L} width={W - L - R} y={bandTop} height={Math.max(0, bandBottom - bandTop)} className="band" />
      {[ref.low, ref.high].filter((v) => v != null).map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="ref" /><text x={L - 5} y={y(v) + 3} textAnchor="end">{v}</text></g>)}
      <polyline points={points.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")} className="trend-line" />
      {points.map((p, i) => <circle key={i} cx={x(p.t)} cy={y(p.v)} r={p.date === selected ? 4.2 : 3} className={`pt ${p.flag === "H" ? "high" : p.flag === "L" ? "low" : ""} ${p.date === selected ? "sel" : ""}`}>
        <title>{`${shortDate(p.date)}: ${fmt(p.v, analyte.decimals)} ${analyte.unit}${p.flag ? ` (${p.flag})` : ""}${p.version > 1 ? ` · corrected v${p.version}` : ""}`}</title></circle>)}
      <text x={L} y={H - 4}>{shortDate(points[0].date)}</text>
      <text x={W - R} y={H - 4} textAnchor="end">{shortDate(points[points.length - 1].date)}</text>
    </svg>
  </div>;
}
