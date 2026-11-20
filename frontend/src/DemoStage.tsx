import React, { useCallback, useEffect, useRef, useState } from "react";
import LiveMonitor from "./LiveMonitor";
import "./demo.css";

/*
 * Pitch-video stage: left, a terminal-style tail of the synthetic observations the
 * live simulation is writing; right, the compact Live Monitor reacting to them.
 * "Play story" resets the simulation and starts the scripted catch-up storyline
 * (routine → missed follow-ups → recall clinic → recovery → stable). Synthetic data only.
 */

const API = import.meta.env.VITE_API_BASE_URL || "/api";
type RecordValue = Record<string, any>;
type Seg = [string, string?];  // text, syntax class
type Line = { id: string, kind: "code" | "phase" | "info", segs?: Seg[], text?: string, tone?: string };

const TICK_SECONDS = 3;
const MAX_LINES = 260;
const SAMPLE_ROWS = 4;        // INSERT rows shown per step; the rest are summarised
const SAMPLE_ASSESSMENTS = 3; // review-state changes walked through per step
const LOINC: Record<string, string> = { "4548-4": "HbA1c", "14749-6": "FPG" };
const THRESHOLDS: Record<string, number> = { hba1c: 6.3, fpg: 7.0 };
const PHASE_TONE: Record<string, string> = { routine: "calm", missed: "warm", catch_up: "hot", recovery: "warm", stable: "calm" };
const SCENARIO = "catch_up";

async function call(path: string, token: string, method = "GET", body?: RecordValue) {
  const response = await fetch(`${API}${path}`, { method, body: body ? JSON.stringify(body) : undefined, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "Request failed");
  return payload;
}

const clock = (iso?: string) => (iso ? new Date(iso) : new Date()).toLocaleTimeString("en-GB", { timeZone: "Asia/Kuala_Lumpur" });
const simDate = (iso?: string | null) => iso ? new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }) : "—";

export default function DemoStage({ token, onExit }: { token: string, onExit: () => void }) {
  const [lines, setLines] = useState<Line[]>([{ id: "boot", kind: "info", text: "sentinel@api:~$ tail -f live_simulation.sql  # statements each step runs" }]);
  const [sim, setSim] = useState<RecordValue | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [chrome, setChrome] = useState(true);
  const [rate, setRate] = useState(0);
  const [monitorKey, setMonitorKey] = useState(0);
  const queue = useRef<Line[]>([]);
  const lastStep = useRef(0);
  const lastPhase = useRef<string | null>(null);
  const primed = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);

  const poll = useCallback(async () => {
    try {
      const dashboard = await call("/live/dashboard?points=2", token);
      const run = dashboard.simulation;
      setSim(run); setError("");
      const steps: RecordValue[] = [...(dashboard.feed || [])].reverse(); // oldest first
      if (!primed.current) { // do not replay history when the stage opens
        primed.current = true; lastStep.current = steps.length ? steps[steps.length - 1].step : 0; lastPhase.current = run?.phase ?? null;
        return;
      }
      const fresh: Line[] = [];
      for (const step of steps.filter((item) => item.step > lastStep.current)) {
        if (step.phase && step.phase !== lastPhase.current) fresh.push({ id: `phase-${step.step}`, kind: "phase", text: `PHASE → ${step.phase_label}`, tone: PHASE_TONE[step.phase] });
        lastPhase.current = step.phase ?? lastPhase.current;
        fresh.push(...stepLines(step, dashboard.statuses || []));
        lastStep.current = step.step;
        setRate(Math.round((step.observations_total || 0) / (run?.tick_seconds || TICK_SECONDS)));
      }
      queue.current.push(...fresh);
    } catch (err: any) { setError(err.message); }
  }, [token]);

  useEffect(() => { poll(); const t = window.setInterval(poll, 1000); return () => window.clearInterval(t); }, [poll]);

  // Typewriter drain: spread each batch over ~1 s so rows visibly stream in.
  useEffect(() => {
    const t = window.setInterval(() => {
      if (!queue.current.length) return;
      const take = Math.max(1, Math.ceil(queue.current.length / 22));
      const next = queue.current.splice(0, take);
      setLines((current) => [...current, ...next].slice(-MAX_LINES));
    }, 45);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight }); }, [lines]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onExit();
      if (event.key.toLowerCase() === "h") setChrome((v) => !v);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onExit]);

  const playStory = async () => {
    try {
      setBusy(true); setError("");
      await call("/live/simulation/reset", token, "POST");
      queue.current = []; primed.current = false;
      setLines([{ id: `boot-${Date.now()}`, kind: "info", text: "sentinel@api:~$ tail -f live_simulation.sql  # statements each step runs" }, { id: `start-${Date.now()}`, kind: "info", text: "-- connected · waiting for the first batch of synthetic lab results…" }]);
      await call("/live/simulation/start", token, "POST", { tick_seconds: TICK_SECONDS, scenario: SCENARIO });
      primed.current = true; lastPhase.current = null; lastStep.current = 0;
      setMonitorKey((k) => k + 1);
    } catch (err: any) { setError(err.message); }
    finally { setBusy(false); }
  };
  const stop = async () => { try { setBusy(true); await call("/live/simulation/stop", token, "POST"); } catch (err: any) { setError(err.message); } finally { setBusy(false); } };

  const running = sim?.status === "running";
  const tone = sim?.phase ? PHASE_TONE[sim.phase] : "idle";

  return <div className={`demo-stage tone-${tone}`}>
    <header className="demo-top">
      <div className="demo-brand"><span className="brand-mark">⌁</span><b>Sentinel Network</b><span>Live ingestion → review monitor</span></div>
      <div className={`demo-phase ${tone}`}><i />{running || sim?.phase ? (sim?.phase_label || "Steady simulation") : "Ready"}</div>
      <div className="demo-meta"><span>Simulated date <b>{simDate(sim?.simulated_now)}</b></span><span>Step <b>{sim?.ticks ?? 0}</b></span></div>
      {chrome && <div className="stage-controls">
        {!running && <button className="primary" disabled={busy} onClick={playStory}>▶ Play story</button>}
        {running && <button className="secondary" disabled={busy} onClick={stop}>■ Stop</button>}
        <button className="link-button" onClick={onExit}>Exit</button>
        <small>H hides controls · Esc exits</small>
      </div>}
    </header>
    {error && <div className="error demo-error">{error}</div>}
    <div className="demo-split">
      <section className="demo-terminal" aria-label="Synthetic observation ingestion log">
        <div className="demo-term-bar"><span className="dots"><i /><i /><i /></span><span>live_simulation.sql · sentinel.db</span><span className="demo-rate">{running ? `${rate} rows/s` : "idle"}</span></div>
        <div className="demo-term-body" ref={scroller}>
          {lines.map((line) => <TermLine key={line.id} line={line} />)}
          <div className="demo-cursor">▍</div>
        </div>
        <div className="demo-term-foot">Synthetic data — demonstration only · writes go through the normal Sync → Analyse → Route scan</div>
      </section>
      <section className="demo-board"><LiveMonitor key={monitorKey} token={token} compact hideControls tickSeconds={TICK_SECONDS} scenario={SCENARIO} /></section>
    </div>
  </div>;
}

function stepLines(step: RecordValue, statuses: RecordValue[]): Line[] {
  const id = `s${step.step}`;
  const out: Line[] = [];
  const line = (n: string, ...segs: Seg[]) => out.push({ id: `${id}-${n}`, kind: "code", segs });
  const label = (key: string) => statuses.find((s) => s.key === key)?.label || key;
  line("h", [`-- step ${step.step} · simulated ${simDate(step.simulated_from)} → ${simDate(step.simulated_to)}`, "c"]);
  line("b", ["BEGIN", "k"], [";"]);
  line("i", ["INSERT INTO ", "k"], ["observations "], ["(patient, loinc, value, unit, observed_at, terminology_status)", "x"]);
  (step.observations || []).slice(0, SAMPLE_ROWS).forEach((row: RecordValue, i: number) => {
    const bad = row.terminology_status !== "verified";
    const high = !bad && row.value >= (THRESHOLDS[row.code] ?? Infinity);
    line(`r${i}`, [i ? "     , " : "VALUES ", i ? "x" : "k"], ["("], [`'${row.patient}'`, "s"], [", "], [`'${row.loinc}'`, "s"], [", "], [Number(row.value).toFixed(1), bad ? "err" : high ? "n hi" : "n"], [", "], [`'${row.unit}'`, bad ? "err" : "s"], [", "], [`'${row.observed_at}'`, "s"], [", "], [`'${row.terminology_status}'`, bad ? "err" : "s"], [")"], [bad ? "  -- ⚠ wrong unit" : `  -- ${LOINC[row.loinc] || row.code}`, bad ? "err" : "c"]);
  });
  if (step.rejected_total) {
    line("w1", [`     -- ⚠ ${step.rejected_total} FPG in mg/dL; LOINC 14749-6 needs mmol/L`, "err"]);
    line("w2", ["     -- ⚠ kept for audit, not used by rules → data_quality_review", "err"]);
  }
  const more = (step.observations_total || 0) - Math.min(SAMPLE_ROWS, (step.observations || []).length);
  if (more > 0) line("m", [`     -- … ${more} more rows in this batch`, "c"]);
  if ((step.corrections || []).length) line("kh", ["INSERT INTO ", "k"], ["observations "], ["(patient, loinc, value, unit, version)  -- corrections", "x"]);
  (step.corrections || []).forEach((row: RecordValue, i: number) => {
    line(`k${i}`, [i ? "     , " : "VALUES ", i ? "x" : "k"], ["("], [`'${row.patient}'`, "s"], [", "], [`'${row.loinc}'`, "s"], [", "], [Number(row.value).toFixed(1), "n"], [", "], ["'mmol/L'", "s"], [", "], [`v${row.version}`, "n"], [")"], [`  -- ✓ was ${row.original_value} ${row.original_unit}`, "ok"]);
  });
  if (step.corrections_total) {
    line("kx", [`     -- ✓ lab re-sent ${step.corrections_total} corrected FPG result(s) as v2`, "ok"]);
    line("ky", ["     -- ✓ v1 kept for audit, v2 supersedes it → re-assessed", "ok"]);
  }
  if ((step.corrections_total || 0) > (step.corrections || []).length) line("km", [`     -- … ${step.corrections_total - step.corrections.length} more corrected results`, "c"]);
  line("e", ["COMMIT", "k"], [";"], [`  -- ${step.observations_total} rows written${step.rejected_total ? `, ${step.rejected_total} with an invalid unit` : ""}${step.corrections_total ? `, ${step.corrections_total} corrections` : ""}`, step.rejected_total ? "err" : "c"]);
  const stages: RecordValue[] = step.scan?.stages || [];
  const stage = (name: string) => stages.find((item) => item.stage === name) || {};
  line("sc", [">>> ", "p"], ["scan", "v"], [" = "], ["execute_scan", "f"], ["(db, trigger="], ['"live_simulation"', "s"], [")"]);
  line("so", ["    sync", "f"], ["(records="], [String(stage("sync").records_received ?? "?"), "n"], [") → "], ["analyse", "f"], ["(patients="], [String(stage("analyse").patients_evaluated ?? "?"), "n"], [") → "], ["route", "f"], ["(new_alerts="], [String(stage("route").new_alerts ?? 0), "n"], [")"]);
  (step.transitions || []).slice(0, SAMPLE_ASSESSMENTS).forEach((row: RecordValue, i: number) => {
    const check = (name: string, value: number | null | undefined, limit: number) => value === null || value === undefined ? [[` ${name} n/a`, "c"]] as Seg[] : [[` ${name} `], [Number(value).toFixed(1), value >= limit ? "n hi" : "n"], [value >= limit ? ` ≥ ${limit} ✓` : ` < ${limit}`, value >= limit ? "hi" : "c"]] as Seg[];
    line(`a${i}`, [">>> ", "p"], ["assessment", "f"], ["("], [`"${row.patient}"`, "s"], [")"]);
    line(`a${i}v`, ["   ", ""], ...check("hba1c", row.hba1c, THRESHOLDS.hba1c), ["  "], ...check("fpg", row.fpg, THRESHOLDS.fpg));
    line(`a${i}t`, ["    "], [label(row.from), `st ${row.from}`], [" → "], [label(row.to), `st ${row.to}`]);
  });
  if (step.overdue !== null && step.overdue !== undefined) line("od", [">>> ", "p"], ["overdue_hba1c", "f"], ["(days="], ["90", "n"], [") → "], [String(step.overdue), step.overdue > 50 ? "n hi" : "n"], [" profiles with no HbA1c in the last 90 days", "c"]);
  const others = (step.transitions_total || 0) - Math.min(SAMPLE_ASSESSMENTS, (step.transitions || []).length);
  line("t", [step.transitions_total ? `-- ${step.transitions_total} profiles changed review state${others > 0 ? ` (${others} not shown)` : ""}` : "-- no review-state changes this step", "c"]);
  line("z", [" "]);
  return out;
}

function TermLine({ line }: { line: Line }) {
  if (line.kind === "code") return <div className="tl code">{(line.segs || []).map(([text, cls], i) => <span key={i} className={cls}>{text}</span>)}</div>;
  return <div className={`tl ${line.kind} ${line.tone || ""}`}>{line.text}</div>;
}
