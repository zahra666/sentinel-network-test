import React from "react";
import { Pill, Syringe } from "lucide-react";
import "./treatment.css";

type RecordValue = Record<string, any>;

const SHORT: Record<string, string> = {
  oral_only: "Oral",
  insulin_plus_oral: "Insulin+oral",
  insulin_only: "Insulin",
  diet_only: "Diet",
  medication_list_needs_review: "Review",
};
export const MODALITY_COLORS: Record<string, string> = {
  oral_only: "#7cb4ff",
  insulin_plus_oral: "#ff7a59",
  insulin_only: "#ff3b1f",
  diet_only: "#4ade80",
  medication_list_needs_review: "#8e8e9e",
};

/** Compact table chip: syringe = injected insulin, pill = oral glucose-lowering drug(s). */
export function TreatmentChip({ treatment, showCount = false }: { treatment?: RecordValue, showCount?: boolean }) {
  if (!treatment) return <span className="tx-chip">n/a</span>;
  const key = treatment.modality;
  const injected = key === "insulin_only" || key === "insulin_plus_oral";
  const oral = key === "oral_only" || key === "insulin_plus_oral";
  return <span className={`tx-chip tx-${key}`} title={`${treatment.label}${oral ? ` · ${treatment.oral_agent_count} oral agent(s)` : ""}`}>
    {injected ? <Syringe size={12} aria-hidden="true" /> : oral ? <Pill size={12} aria-hidden="true" /> : null}
    {SHORT[key] || treatment.label}{showCount && oral && treatment.oral_agent_count > 1 && <small>×{treatment.oral_agent_count}</small>}
  </span>;
}

/** Patient detail: active glucose-lowering list with route. */
export function TherapyList({ treatment }: { treatment?: RecordValue }) {
  if (!treatment) return null;
  return <div className="card-section">
    <h4>Glucose-lowering therapy <TreatmentChip treatment={treatment} showCount /></h4>
    {treatment.medications.length ? <ul className="tx-list">{treatment.medications.map((m: RecordValue, i: number) =>
      <li key={i}>{m.route === "injection" ? <Syringe size={14} /> : <Pill size={14} />}<span>{m.display}</span>
        <small>{m.route === "injection" ? "Injection" : m.route === "oral" ? "Oral" : "Unmapped"}</small><code>{m.atc_code || "local"}</code></li>)}</ul>
      : <p className="body-text">No glucose-lowering medication recorded (diet / lifestyle management).</p>}
  </div>;
}

/** Cohort modality mix next to the Malaysian NDR 2023 audit. */
export function TreatmentSummary({ summary }: { summary: RecordValue }) {
  const rows: RecordValue[] = summary.modalities;
  const total = summary.patients || 1;
  const segments = [...rows, ...(summary.needs_review ? [{ key: "medication_list_needs_review", label: "Needs review", count: summary.needs_review, cohort_pct: Math.round(1000 * summary.needs_review / total) / 10 }] : [])];
  return <div className="tx-summary">
    <div className="tx-bar" role="img" aria-label="Treatment modality mix">
      {segments.map((s) => <i key={s.key} style={{ width: `${(100 * s.count) / total}%`, background: MODALITY_COLORS[s.key] }} title={`${s.label}: ${s.count}`} />)}
    </div>
    <table className="table compact tx-table">
      <thead><tr><th>Modality</th><th className="num">Patients</th><th className="num">Cohort</th><th className="num">NDR 2023</th></tr></thead>
      <tbody>
        {rows.map((r) => <tr key={r.key}><td><i className="tx-dot" style={{ background: MODALITY_COLORS[r.key] }} />{r.label}{r.derived && <sup>*</sup>}</td>
          <td className="num">{r.count}</td><td className="num"><b>{r.cohort_pct.toFixed(1)}%</b></td><td className="num">{r.ndr_2023_pct.toFixed(2)}%</td></tr>)}
        {summary.detail.map((r: RecordValue) => <tr key={r.label} className="tx-sub"><td>{r.label}</td><td /><td className="num">{r.cohort_pct.toFixed(1)}%</td><td className="num">{r.ndr_2023_pct.toFixed(2)}%</td></tr>)}
      </tbody>
    </table>
    <p className="tx-foot">
      Reference: <a href={summary.reference.url} target="_blank" rel="noreferrer">{summary.reference.source}</a>. * {summary.reference.note}
      {summary.needs_review ? ` ${summary.needs_review} record(s) carry a deliberately unmapped drug code and are shown as needing review.` : ""} Synthetic data; not a prescribing model.
    </p>
  </div>;
}

/** Aggregate cohort profile (sex, hypertension, eGFR) next to Malaysian references. */
export function CohortProfile({ profile }: { profile: RecordValue }) {
  const htn = profile.hypertension, egfr = profile.egfr, sex = profile.sex;
  const tested = profile.patients - egfr.not_tested || 1;
  const rows: [string, string, string][] = [
    ["Female / male", `${sex.female?.count ?? 0} / ${sex.male?.count ?? 0}`, "57.08% F"],
    ["Hypertension", `${htn.count} (${htn.pct}%)`, `${htn.ndr_2023_pct.toFixed(2)}%`],
    ["  · female / male", `${htn.by_sex.female ?? 0} / ${htn.by_sex.male ?? 0}`, ""],
    ["eGFR not tested", `${egfr.not_tested} (${egfr.not_tested_pct}%)`, "9.01%"],
    ["eGFR < 60 (of tested)", `${egfr.below_60} (${(100 * egfr.below_60 / tested).toFixed(1)}%)`, "22.4%"],
  ];
  return <div className="tx-summary">
    <table className="table compact tx-table">
      <thead><tr><th>Measure</th><th className="num">Cohort</th><th className="num">Malaysia</th></tr></thead>
      <tbody>{rows.map(([label, value, ref]) => <tr key={label} className={label.startsWith("  ") ? "tx-sub" : ""}><td>{label.trim()}</td><td className="num"><b>{value}</b></td><td className="num">{ref}</td></tr>)}</tbody>
    </table>
    <p className="tx-foot">Malaysia: NDR Report 2023 (sex Table 2, hypertension Table 5, creatinine testing Table 6); eGFR &lt; 60 from the NDR 2022 audit (Sci Rep 2024). Hypertension = active, verified {htn.code}; counts recorded diagnoses, not blood-pressure control. Synthetic data.</p>
  </div>;
}
