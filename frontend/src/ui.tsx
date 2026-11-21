import React, { useEffect, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";

export const STATUS_COLORS: Record<string, string> = {
  no_qualifying_evidence: "#34d399",
  confirmation_gap: "#fbbf24",
  evidence_requires_clinician_review: "#ff3b1f",
  data_quality_review: "#8e8ea0",
  out_of_scope: "#3b3b4a",
};

export type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "brand";

const LABELS: Record<string, string> = {
  not_run: "Not run",
  no_qualifying_evidence: "No qualifying evidence",
  evidence_requires_clinician_review: "Clinician review required",
  confirmation_gap: "Confirmation gap",
  data_quality_review: "Data quality review",
  out_of_scope: "Out of scope",
  pending_or_unavailable: "Not anchored",
  pending: "Pending",
  confirmed: "Confirmed",
  sync: "Sync",
  analyse: "Analyse",
  route: "Route",
  llm_planned: "Planned",
  unit_incompatible: "Unit issue",
  needs_terminology_review: "Needs review",
};

/** Turns API enum values such as `not_run` into readable labels. */
export function human(value?: string | null): string {
  if (value === undefined || value === null || value === "") return "N/A";
  const key = String(value);
  if (LABELS[key]) return LABELS[key];
  const text = key.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const TONES: Record<string, Tone> = {
  completed: "success", confirmed: "success", healthy: "success", implemented: "success", resolved: "success", sent: "success", verified: "success", enabled: "success", running: "success",
  acknowledged: "info", new: "warning", awaiting_approval: "warning", partial: "warning", overdue: "warning", confirmation_gap: "warning", data_quality_review: "neutral",
  evidence_requires_clinician_review: "danger", failed: "danger", error: "danger", gap: "danger",
  pending: "neutral", not_run: "neutral", planned: "neutral", manual: "neutral", disabled: "neutral", no_qualifying_evidence: "success", out_of_scope: "neutral",
};

export function toneFor(value?: string | null): Tone {
  if (!value) return "neutral";
  const key = String(value).toLowerCase();
  if (TONES[key]) return TONES[key];
  if (key.includes("unavailable") || key.includes("pending")) return "neutral";
  if (key.includes("fail") || key.includes("error")) return "danger";
  return "neutral";
}

export function Badge({ tone = "neutral", children }: { tone?: Tone, children: React.ReactNode }) {
  return <span className={`badge tone-${tone}`}>{children}</span>;
}

type CardProps = { title?: string, subtitle?: string, count?: number, actions?: React.ReactNode, flush?: boolean, className?: string, children?: React.ReactNode };
export function Card({ title, subtitle, count, actions, flush, className, children }: CardProps) {
  return <section className={`card${flush ? " flush" : ""}${className ? ` ${className}` : ""}`}>
    {(title || actions) && <header className="card-header"><div><h3>{title}{count !== undefined && <span className="count">{count.toLocaleString()}</span>}</h3>{subtitle && <p>{subtitle}</p>}</div>{actions && <div className="card-actions">{actions}</div>}</header>}
    <div className="card-body">{children}</div>
  </section>;
}

export function Stat({ label, value, hint, icon: Icon, tone }: { label: string, value: React.ReactNode, hint?: string, icon?: LucideIcon, tone?: Tone }) {
  const long = typeof value === "string" && value.length > 10;
  return <article className={`stat${tone ? ` tone-${tone}` : ""}${long ? " long" : ""}`}>
    <div className="stat-head"><span>{label}</span>{Icon && <Icon size={16} strokeWidth={1.9} />}</div>
    <strong>{typeof value === "number" ? <CountUp value={value} /> : value}</strong>
    {hint && <small>{hint}</small>}
  </article>;
}

export function Empty({ title, text }: { title: string, text?: string }) {
  return <div className="empty"><b>{title}</b>{text && <span>{text}</span>}</div>;
}

/** Animates a number from its previous value to the new one. */
export function CountUp({ value, suffix = "" }: { value: number, suffix?: string }) {
  const [shown, setShown] = useState(value);
  const from = useRef(0);
  useEffect(() => {
    const start = performance.now(), begin = from.current, change = value - begin, duration = 700;
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration), eased = 1 - Math.pow(1 - t, 3);
      setShown(Math.round(begin + change * eased));
      if (t < 1) frame = requestAnimationFrame(tick); else from.current = value;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return <>{shown.toLocaleString()}{suffix}</>;
}

/** Circular gauge used for headline percentages. */
export function Ring({ value, size = 220, stroke = 14, color = "var(--red)", children }: { value: number, size?: number, stroke?: number, color?: string, children?: React.ReactNode }) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, pct = Math.max(0, Math.min(100, value));
  return <div className="ring" style={{ width: size, height: size }}>
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--ring-track)" strokeWidth={stroke} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round" strokeDasharray={`${(pct / 100) * c} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} className="ring-value" />
    </svg>
    <div className="ring-center">{children}</div>
  </div>;
}

/** Minimal trend line for a short series of values. */
export function Sparkline({ values, width = 120, height = 36, color = "var(--red)" }: { values: number[], width?: number, height?: number, color?: string }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * (width - 4) + 2, height - 3 - ((v - lo) / span) * (height - 6)]);
  const last = pts[pts.length - 1];
  return <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
    <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    <circle cx={last[0]} cy={last[1]} r="3" fill={color} />
  </svg>;
}
