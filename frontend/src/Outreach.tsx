import React, { useEffect, useState } from "react";
import { ChevronDown, Sparkles } from "lucide-react";
import { Badge, Card, Empty, Stat, type Tone } from "./ui";
import "./outreach.css";

const API = import.meta.env.VITE_API_BASE_URL || "/api";
type RecordValue = Record<string, any>;

async function api(path: string, token: string, options: RequestInit = {}) {
  const response = await fetch(`${API}${path}`, { ...options, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(options.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (HTTP ${response.status})`);
  return body;
}

const templateLabel: Record<string, string> = { review_appointment: "Review appointment", repeat_test: "Repeat blood test", routine_follow_up: "Routine check-up" };
const statusLabel: Record<string, string> = { awaiting_approval: "Awaiting approval", sent: "Sent", discarded: "Discarded" };
const statusTone: Record<string, Tone> = { awaiting_approval: "warning", sent: "success", discarded: "neutral" };

function MessageCard({ message, token, onChange, compact }: { message: RecordValue, token: string, onChange: () => Promise<void>, compact?: boolean }) {
  const [open, setOpen] = useState(!compact);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (action: "approve" | "discard") => {
    try { setBusy(true); setError(""); await api(`/outreach/messages/${message.id}/${action}`, token, { method: "POST" }); await onChange(); }
    catch (err: any) { setError(err.message); } finally { setBusy(false); }
  };
  const pending = message.status === "awaiting_approval";
  return <article className={`message-card${open ? " open" : ""}`}>
    <button className="message-row" onClick={() => setOpen(!open)} aria-expanded={open}>
      <span className="message-who"><b>{message.patient_reference}</b><span>{templateLabel[message.template] || message.template}</span></span>
      <Badge tone={message.includes_results ? "info" : "neutral"}>{message.includes_results ? "Includes results" : "Reminder only"}</Badge>
      <Badge tone={statusTone[message.status] || "neutral"}>{statusLabel[message.status] || message.status}</Badge>
      <ChevronDown size={16} className="chevron" />
    </button>
    {open && <div className="message-body">
      <dl className="email-meta"><dt>To</dt><dd><code>{message.recipient}</code></dd><dt>Subject</dt><dd><b>{message.subject}</b></dd><dt>Drafted by</dt><dd>Goola Agent</dd>{message.approved_by && <><dt>Approved by</dt><dd>{message.approved_by}</dd></>}</dl>
      <pre>{message.body}</pre>
      {error && <div className="error">{error}</div>}
      {pending && <div className="message-actions"><button className="primary small" disabled={busy} onClick={() => act("approve")}>Approve and send</button><button className="ghost small" disabled={busy} onClick={() => act("discard")}>Discard</button></div>}
    </div>}
  </article>;
}

export function PatientOutreach({ patientId, token }: { patientId: string, token: string }) {
  const [data, setData] = useState<RecordValue | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => { try { setError(""); setData(await api(`/outreach/patients/${patientId}`, token)); } catch (err: any) { setError(err.message); } };
  useEffect(() => { setData(null); load(); }, [patientId]);
  const draft = async () => { try { setBusy(true); setError(""); await api(`/outreach/patients/${patientId}/draft`, token, { method: "POST" }); await load(); } catch (err: any) { setError(err.message); } finally { setBusy(false); } };
  const setConsent = async (next: RecordValue) => { try { setError(""); await api(`/outreach/patients/${patientId}/consent`, token, { method: "POST", body: JSON.stringify(next) }); await load(); } catch (err: any) { setError(err.message); } };
  if (!data) return error ? <div className="error">{error}</div> : <div className="loading-line short" />;
  const consent = data.consent;
  return <div className="patient-outreach">
    <div className="consent-row">
      <div className="consent">
        <label className="check"><input type="checkbox" checked={consent.email_reminders} onChange={(event) => setConsent({ email_reminders: event.target.checked, share_results: consent.share_results && event.target.checked })} />Email reminders</label>
        <label className="check"><input type="checkbox" checked={consent.share_results} disabled={!consent.email_reminders} onChange={(event) => setConsent({ email_reminders: consent.email_reminders, share_results: event.target.checked })} />Share results by email</label>
      </div>
      <button className="secondary small" disabled={busy || !consent.email_reminders} onClick={draft}><Sparkles size={14} />{busy ? "Drafting…" : "Draft reminder"}</button>
    </div>
    <p className="contact">{consent.contact_email}</p>
    {error && <div className="error">{error}</div>}
    {data.messages.length ? <div className="message-list">{data.messages.map((message: RecordValue) => <MessageCard key={message.id} message={message} token={token} onChange={load} compact={message.status !== "awaiting_approval"} />)}</div> : null}
  </div>;
}

export default function OutreachPage({ token }: { token: string }) {
  const [data, setData] = useState<RecordValue | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => { try { setError(""); setData(await api("/outreach/outbox", token)); } catch (err: any) { setError(err.message); } };
  useEffect(() => { load(); }, [token]);
  const batch = async () => {
    try {
      setBusy(true); setError("");
      const result = await api("/outreach/drafts/open-alerts", token, { method: "POST" });
      const parts = [`${result.drafted.length} reminder${result.drafted.length === 1 ? "" : "s"} drafted`];
      if (result.opted_out.length) parts.push(`${result.opted_out.length} skipped (opted out)`);
      if (result.already_waiting.length) parts.push(`${result.already_waiting.length} already waiting`);
      setNotice(`${parts.join(", ")}.`);
      await load();
    } catch (err: any) { setError(err.message); } finally { setBusy(false); }
  };
  const messages: RecordValue[] = data?.messages || [];
  const pending = messages.filter((item) => item.status === "awaiting_approval");
  const done = messages.filter((item) => item.status !== "awaiting_approval");
  return <div className="stack-lg">
    <section className="outreach-top">
      <Stat label="Awaiting approval" value={data?.awaiting_approval ?? 0} tone={data?.awaiting_approval ? "warning" : undefined} />
      <Stat label="Sent" value={data?.sent ?? 0} />
      <div className="outreach-cta"><button className="primary" disabled={busy} onClick={batch}><Sparkles size={15} />{busy ? "Drafting…" : "Draft reminders for open alerts"}</button></div>
    </section>
    {notice && <div className="success">{notice}</div>}{error && <div className="error">{error}</div>}
    <section className="grid-2">
      <Card title="Awaiting approval" count={pending.length}>{pending.length ? <div className="message-list">{pending.map((message) => <MessageCard key={message.id} message={message} token={token} onChange={load} compact />)}</div> : <Empty title="Nothing to approve" text="Drafted reminders appear here for review." />}</Card>
      <Card title="Outbox" count={done.length}>{done.length ? <div className="message-list">{done.map((message) => <MessageCard key={message.id} message={message} token={token} onChange={load} compact />)}</div> : <Empty title="Outbox is empty" text="Approved reminders are listed here." />}</Card>
    </section>
  </div>;
}
