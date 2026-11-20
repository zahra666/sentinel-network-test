import React, { useCallback, useEffect, useRef, useState } from "react";
import LiveMonitor from "./LiveMonitor";
import "./demo.css";

/*
 * Pitch-video stage: left, a terminal-style tail of the synthetic observations the
 * live simulation is writing; right, the compact Live Monitor reacting to them.
 * "Play story" resets the simulation and starts the scripted surge scenario
 * (baseline → surge → recovery → stable). Synthetic data only.
 */

const API = import.meta.env.VITE_API_BASE_URL || "/api";
type RecordValue = Record<string, any>;
type Line = { id: string, kind: "obs" | "scan" | "phase" | "info" | "more", text?: string, obs?: RecordValue, at?: string, tone?: string };

const TICK_SECONDS = 3;
const MAX_LINES = 220;
const THRESHOLDS: Record<string, number> = { hba1c: 6.3, fpg: 7.0 };
const PHASE_TONE: Record<string, string> = { baseline: "calm", surge: "hot", recovery: "warm", stable: "calm" };

async function call(path: string, token: string, method = "GET", body?: RecordValue) {
  const response = await fetch(`${API}${path}`, { method, body: body ? JSON.stringify(body) : undefined, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "Request failed");
  return payload;
}

const clock = (iso?: string) => (iso ? new Date(iso) : new Date()).toLocaleTimeString("en-GB", { timeZone: "Asia/Kuala_Lumpur" });
const simDate = (iso?: string | null) => iso ? new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }) : "—";

export default function DemoStage({ token, onExit }: { token: string, onExit: () => void }) {
  const [lines, setLines] = useState<Line[]>([{ id: "boot", kind: "info", text: "sentinel@ingest:~$ tail -f observations --source=live-simulation" }]);
  const [sim, setSim] = useState<RecordValue | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [chrome, setChrome] = useState(true);
  const [rate, setRate] = useState(0);
  const [monitorKey, setMonitorKey] = useState(0);
  const seen = useRef<Set<string>>(new Set());
  const queue = useRef<Line[]>([]);
  const lastScan = useRef<string | null>(null);
  const lastPhase = useRef<string | null>(null);
  const lastWritten = useRef<number | null>(null);
  const primed = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);

  const poll = useCallback(async () => {
    try {
      const feed = await call("/live/feed?limit=120", token);
      setSim(feed.simulation); setError("");
      const fresh: Line[] = [];
      const run = feed.simulation;
      const observations: RecordValue[] = [...feed.observations].reverse(); // oldest first
      const unseen = observations.filter((o) => !seen.current.has(o.id));
      unseen.forEach((o) => seen.current.add(o.id));
      if (!primed.current) { // do not replay history when the stage opens
        primed.current = true; lastScan.current = feed.latest_scan?.id ?? null; lastPhase.current = run?.phase ?? null; lastWritten.current = run?.observations_written ?? 0;
        return;
      }
      if (run?.phase && run.phase !== lastPhase.current && run.status === "running") {
        fresh.push({ id: `phase-${run.ticks}`, kind: "phase", text: `PHASE → ${run.phase_label}`, tone: PHASE_TONE[run.phase] });
      }
      lastPhase.current = run?.phase ?? null;
      const written = run?.observations_written ?? 0;
      const delta = lastWritten.current === null ? 0 : Math.max(0, written - lastWritten.current);
      lastWritten.current = written;
      fresh.push(...unseen.map((obs) => ({ id: obs.id, kind: "obs" as const, obs })));
      if (delta > unseen.length) fresh.push({ id: `more-${written}`, kind: "more", text: `… +${delta - unseen.length} more rows committed in this batch (${delta} total)` });
      const scan = feed.latest_scan;
      if (scan && scan.id !== lastScan.current) {
        lastScan.current = scan.id;
        fresh.push({ id: `scan-${scan.id}`, kind: "scan", text: `SCAN ${scan.id.slice(0, 6)} · Sync → Analyse → Route · ${scan.scanned_count} profiles evaluated · ${scan.finding_count} new review findings`, at: scan.started_at });
      }
      if (delta) setRate(Math.round(delta / (run?.tick_seconds || TICK_SECONDS)));
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
      queue.current = []; seen.current = new Set(); primed.current = false; lastWritten.current = 0;
      setLines([{ id: `boot-${Date.now()}`, kind: "info", text: "sentinel@ingest:~$ tail -f observations --source=live-simulation" }, { id: `start-${Date.now()}`, kind: "info", text: "connected · waiting for HL7/FHIR result batches…" }]);
      await call("/live/simulation/start", token, "POST", { tick_seconds: TICK_SECONDS, scenario: "surge" });
      primed.current = true; lastPhase.current = null; lastScan.current = null;
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
        <div className="demo-term-bar"><span className="dots"><i /><i /><i /></span><span>ingest · observations</span><span className="demo-rate">{running ? `${rate} rows/s` : "idle"}</span></div>
        <div className="demo-term-body" ref={scroller}>
          {lines.map((line) => <TermLine key={line.id} line={line} />)}
          <div className="demo-cursor">▍</div>
        </div>
        <div className="demo-term-foot">Synthetic data — demonstration only · writes go through the normal Sync → Analyse → Route scan</div>
      </section>
      <section className="demo-board"><LiveMonitor key={monitorKey} token={token} compact hideControls tickSeconds={TICK_SECONDS} scenario="surge" /></section>
    </div>
  </div>;
}

function TermLine({ line }: { line: Line }) {
  if (line.kind === "obs" && line.obs) {
    const o = line.obs;
    const high = (o.normalised_value ?? o.value) >= (THRESHOLDS[o.code] ?? Infinity);
    return <div className="tl obs">
      <span className="t">{clock(o.ingested_at)}</span>
      <span className="k">INSERT</span>
      <span className="p">{o.patient_reference}</span>
      <span className="s">{String(o.care_setting).slice(0, 14)}</span>
      <span className="c">{o.code === "hba1c" ? "HbA1c" : "FPG"}</span>
      <span className={high ? "v high" : "v"}>{Number(o.value).toFixed(1)} {o.unit}</span>
      <span className="l">LOINC {o.standard_code} {o.terminology_status === "verified" ? "✓" : "?"}</span>
    </div>;
  }
  return <div className={`tl ${line.kind} ${line.tone || ""}`}>{line.at && <span className="t">{clock(line.at)}</span>}{line.text}</div>;
}
