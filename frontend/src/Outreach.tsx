import React, { useEffect, useState } from "react";
import "./outreach.css";

const API = import.meta.env.VITE_API_BASE_URL || "/api";
type RecordValue = Record<string, any>;

async function api(path: string, token: string, options: RequestInit = {}) {
  const response = await fetch(`${API}${path}`, { ...options, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(options.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (HTTP ${response.status})`);
  return body;
}

const templateLabel: Record<string, string> = { review_appointment: "Review appointment", repeat_test: "Repeat blood test", routine_follow_up: "Routine check-up" };
const statusLabel: Record<string, string> = { awaiting_approval: "Awaiting approval", sent: "Sent (simulated)", discarded: "Discarded" };

function MessageCard({ message, token, onChange, compact }: { message: RecordValue, token: string, onChange: () => Promise<void>, compact?: boolean }) {
  const [open, setOpen] = useState(!compact);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (action: "approve" | "discard") => {
    try { setBusy(true); setError(""); await api(`/outreach/messages/${message.id}/${action}`, token, { method: "POST" }); await onChange(); }
    catch (err: any) { setError(err.message); } finally { setBusy(false); }
  };
  const pending = message.status === "awaiting_approval";
  return <article className={`outreach-message ${message.status}`}>
    <header onClick={() => setOpen(!open)}>
      <div><b>{message.patient_reference}</b><span className="outreach-template">{templateLabel[message.template] || message.template}</span>
        <span className={message.includes_results ? "outreach-chip consent" : "outreach-chip"}>{message.includes_results ? "Includes results · consent on file" : "Reminder only · no health data"}</span></div>
      <span className={`outreach-status ${message.status}`}>{statusLabel[message.status] || message.status}</span>
    </header>
    {open && <div className="outreach-email">
      <div className="outreach-meta"><span>To</span><code>{message.recipient}</code><span>Subject</span><b>{message.subject}</b><span>Drafted by</span><em>Goola Agent ✦</em>{message.approved_by && <><span>Approved by</span><em>{message.approved_by}</em></>}</div>
      <pre>{message.body}</pre>
      {error && <div className="error">{error}</div>}
      {pending && <div className="outreach-actions"><button className="primary" disabled={busy} onClick={() => act("approve")}>Approve &amp; send</button><button className="link-button" disabled={busy} onClick={() => act("discard")}>Discard draft</button><small>Demo outbox: the address uses the reserved .invalid domain, so no real email leaves the system.</small></div>}
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
  if (!data) return <div className="outreach-panel">{error ? <div className="error">{error}</div> : <small>Loading outreach…</small>}</div>;
  const consent = data.consent;
  return <div className="outreach-panel">
    <div className="outreach-panel-head"><b>Patient outreach</b><button className="secondary" disabled={busy || !consent.email_reminders} onClick={draft}>✦ Ask agent to draft reminder</button></div>
    <div className="outreach-consent">
      <label><input type="checkbox" checked={consent.email_reminders} onChange={(event) => setConsent({ email_reminders: event.target.checked, share_results: consent.share_results && event.target.checked })} /> Agrees to email reminders</label>
      <label><input type="checkbox" checked={consent.share_results} disabled={!consent.email_reminders} onChange={(event) => setConsent({ email_reminders: consent.email_reminders, share_results: event.target.checked })} /> Agrees to receive results by email</label>
      <small>Consent recorded in clinic · {consent.contact_email}</small>
    </div>
    {error && <div className="error">{error}</div>}
    {data.messages.map((message: RecordValue) => <MessageCard key={message.id} message={message} token={token} onChange={load} compact={message.status !== "awaiting_approval"} />)}
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
      setNotice(`Goola Agent drafted ${result.drafted.length} reminder(s)${result.opted_out.length ? `, skipped ${result.opted_out.length} who opted out of email` : ""}${result.already_waiting.length ? `, ${result.already_waiting.length} already waiting` : ""}. Nothing is sent until you approve.`);
      await load();
    } catch (err: any) { setError(err.message); } finally { setBusy(false); }
  };
  const messages: RecordValue[] = data?.messages || [];
  const pending = messages.filter((item) => item.status === "awaiting_approval");
  const done = messages.filter((item) => item.status !== "awaiting_approval");
  return <section className="outreach-layout">
    <section className="outreach-hero">
      <div><p className="eyebrow">AGENT DRAFTS · STAFF APPROVE</p><h2>Patient reminders without the paperwork.</h2>
        <p>Goola Agent writes a reminder for each patient who needs follow-up. Results are included only when the patient agreed; otherwise the email is a plain reminder. Nothing is sent until a staff member approves it.</p>
        <button className="primary" disabled={busy} onClick={batch}>✦ Draft reminders for patients with open alerts</button></div>
      <div className="outreach-counts"><div><strong>{data?.awaiting_approval ?? "—"}</strong><span>Awaiting approval</span></div><div><strong>{data?.sent ?? "—"}</strong><span>Sent (simulated)</span></div></div>
    </section>
    {notice && <div className="success">{notice}</div>}{error && <div className="error">{error}</div>}
    <section className="outreach-columns">
      <div><h3>Awaiting approval</h3>{pending.length ? pending.map((message) => <MessageCard key={message.id} message={message} token={token} onChange={load} compact />) : <div className="empty">No drafts waiting. Ask the agent to draft reminders.</div>}</div>
      <div><h3>Outbox</h3>{done.length ? done.map((message) => <MessageCard key={message.id} message={message} token={token} onChange={load} compact />) : <div className="empty">Approved reminders appear here.</div>}</div>
    </section>
  </section>;
}
