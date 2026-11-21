from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cohort import cohort_profile
from .chain import verify_batch
from .care_gap import care_gaps_for_user
from .outreach import draft_for_open_alerts, draft_message, find_reference
from .models import AgentConversation, AgentMessage, AuditEvent, DelegatedTask, Observation, Patient, SourceBatch, User

ALLOWED_CRAWL_HOSTS = {"moh.gov.my", "www.moh.gov.my", "mems.my", "www.mems.my"}
ALLOWED_TASKS = {"evidence_reviewer", "regional_analyst", "integrity_verifier", "source_crawler"}


def enqueue_task(db: Session, user: User, task_type: str, input_data: dict) -> DelegatedTask:
    if task_type not in ALLOWED_TASKS:
        raise ValueError("Unsupported subagent task")
    if task_type == "source_crawler" and user.role != "administrator":
        raise PermissionError("Only an administrator can request a public-source crawl")
    task = DelegatedTask(requester_id=user.id, task_type=task_type, input_data=input_data)
    db.add(task)
    db.flush()
    db.add(AuditEvent(actor_id=user.id, event_type="agent.subagent_queued", entity_type="delegated_task", entity_id=task.id, detail={"task_type": task_type}))
    return task


def process_task(db: Session, task: DelegatedTask) -> DelegatedTask:
    requester = db.get(User, task.requester_id)
    task.status = "running"
    if task.task_type == "evidence_reviewer":
        patient = db.get(Patient, task.input_data.get("patient_id"))
        if not patient or not requester or (requester.role == "clinician" and requester.organization_id != patient.organization_id):
            task.output_data = {"error": "Authorised patient evidence is unavailable."}
        else:
            observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at)).all())
            task.output_data = {"patient_reference": patient.reference, "observation_count": len(observations), "instruction": "Use the CPG review tree in the patient record; no diagnosis or treatment recommendation is generated."}
    elif task.task_type == "integrity_verifier":
        batch = db.get(SourceBatch, task.input_data.get("batch_id"))
        observations = list(db.scalars(select(Observation)).all())
        task.output_data = verify_batch(batch, observations) if batch else {"error": "Batch not found."}
    elif task.task_type == "regional_analyst":
        task.output_data = {"instruction": "Regional analysis is returned only through the fixed, authorised aggregate endpoint.", "status": "handoff_required"}
    else:
        task.output_data = _crawl_public_source(task.input_data.get("url", ""))
    task.status = "completed"
    task.completed_at = datetime.now(timezone.utc)
    db.add(AuditEvent(actor_id=task.requester_id, event_type="agent.subagent_completed", entity_type="delegated_task", entity_id=task.id, detail={"task_type": task.task_type, "status": task.status}))
    return task


def process_pending_tasks(db: Session) -> int:
    tasks = list(db.scalars(select(DelegatedTask).where(DelegatedTask.status == "queued")).all())
    for task in tasks:
        process_task(db, task)
    return len(tasks)


def chat(db: Session, user: User, content: str, conversation_id: str | None = None) -> dict:
    conversation = db.get(AgentConversation, conversation_id) if conversation_id else None
    if conversation and conversation.user_id != user.id:
        raise PermissionError("Conversation is not available to this user")
    if not conversation:
        conversation = AgentConversation(user_id=user.id)
        db.add(conversation)
        db.flush()
    db.add(AgentMessage(conversation_id=conversation.id, role="user", content=content, tool_events=[]))
    text = content.strip().lower()
    events: list[dict] = []
    if text.startswith("crawl "):
        task = enqueue_task(db, user, "source_crawler", {"url": content.strip()[6:].strip()})
        answer = "I queued an allowlisted public-source crawl. It will fetch reference material only; no clinical records leave Goola."
        events.append({"type": "source_crawler", "task_id": task.id})
    elif "integrity" in text or "blockchain" in text:
        batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at.desc()))
        task = enqueue_task(db, user, "integrity_verifier", {"batch_id": batch.id if batch else ""})
        answer = "I queued the integrity verifier against the current source batch. It will return commitment evidence, not a fabricated receipt."
        events.append({"type": "integrity_verifier", "task_id": task.id})
    elif any(word in text for word in ("email", "e-mail", "remind", "message patient", "contact patient")):
        answer, events = _draft_reminders(db, user, content)
    elif _is_hypertension_question(text):
        answer, events = _hypertension_count(db, user)
    elif _is_sglt2_review(text):
        answer, events = _sglt2_review(db, user)
    elif "patient" in text or "evidence" in text:
        answer = "For an evidence review, open an authorised patient record and choose ‘Delegate evidence review’. I will enforce your organisation boundary and return only rule evidence and data-quality context."
    elif "regional" in text or "district" in text:
        answer = "Regional analysis is limited to authorised, fixed aggregate queries with denominator and small-cell controls. I can hand that to the aggregate-analysis subagent after grant verification."
    else:
        answer = "I am Goola Agent. I can queue an authorised evidence review, integrity verification, fixed aggregate-analysis handoff, or an administrator-approved crawl of MOH/MEMS public guidance. I can list T2DM records with ASCVD or eGFR < 60 and no active SGLT2-inhibitor entry for clinician medication review (try: ‘list T2DM patients with ASCVD or eGFR <60 not yet on SGLT2-i’). I can also draft patient reminder emails (try: ‘email patients with open alerts’ or ‘remind SYN-DM-001’); a staff member must approve each one before it is sent. I can count records with a hypertension diagnosis (try: ‘how many patients have high blood pressure?’). I do not diagnose, prescribe, or access data outside your role."
    db.add(AgentMessage(conversation_id=conversation.id, role="assistant", content=answer, tool_events=events))
    db.add(AuditEvent(actor_id=user.id, event_type="agent.chat", entity_type="agent_conversation", entity_id=conversation.id, detail={"tool_events": events}))
    return {"conversation_id": conversation.id, "message": answer, "tool_events": events}


def _is_hypertension_question(text: str) -> bool:
    words = set(text.replace("?", " ").replace(",", " ").split())
    return "blood pressure" in text or "hypertensi" in text or "高血压" in text or bool(words & {"bp", "htn"})


def _hypertension_count(db: Session, user: User) -> tuple[str, list[dict]]:
    """Aggregate count only (no identifiers), bounded to the caller's organisation for clinicians."""
    if user.role not in {"administrator", "clinician"}:
        return "Only administrators and clinicians can view cohort counts.", []
    query = select(Patient)
    if user.role == "clinician":
        query = query.where(Patient.organization_id == user.organization_id)
    profile = cohort_profile(list(db.scalars(query).all()))
    htn = profile["hypertension"]
    scope = "your hospital" if user.role == "clinician" else "all three hospitals"
    by_sex = htn["by_sex"]
    answer = (f"{htn['count']} of {profile['patients']} synthetic T2DM records in {scope} ({htn['pct']}%) have an active, verified "
              f"hypertension diagnosis (SNOMED CT 38341003): {by_sex.get('female', 0)} female and {by_sex.get('male', 0)} male. "
              f"For reference, the Malaysian National Diabetes Registry 2023 audit reports hypertension in {htn['ndr_2023_pct']}% of people with T2DM. "
              "This is a count of recorded diagnoses, not of current blood-pressure control.")
    return answer, [{"type": "cohort_count", "measure": "hypertension", "count": htn["count"], "denominator": profile["patients"]}]


def _is_sglt2_review(text: str) -> bool:
    compact = text.replace("-", "").replace(" ", "")
    return "sglt2" in compact or "caregap" in compact


_STATE_LABEL = {
    "review_candidate": "for medication review",
    "below_initiation_threshold": "eGFR below initiation threshold",
    "medication_list_needs_review": "medication list needs terminology review",
    "insufficient_evidence": "insufficient evidence",
}


def _sglt2_review(db: Session, user: User) -> tuple[str, list[dict]]:
    if user.role not in {"administrator", "clinician"}:
        return "Only administrators and clinicians can run the SGLT2 medication-list review.", []
    result = care_gaps_for_user(db, user, limit=50)
    counts = result["counts"]
    scope = "your organisation" if result["scope"] == "organisation" else "all three synthetic hospitals"
    candidates = [row for row in result["records"] if row["state"] == "review_candidate"]
    lines = [f"I reviewed {result['reviewed_records']} synthetic T2DM records in {scope} against the ASCVD / CKD-EPI eGFR < 60 criterion.",
             f"{counts.get('review_candidate', 0)} meet the criterion with no active, verified SGLT2-inhibitor entry; "
             f"{counts.get('on_sglt2_inhibitor', 0)} meeting it are already on one."]
    if candidates:
        shown = ", ".join(f"{row['patient_reference']} ({' + '.join('ASCVD' if c['criterion'] == 'ascvd' else 'eGFR ' + format(c['egfr'], 'g') for c in row['criteria_met'])})" for row in candidates[:10])
        more = f" and {len(candidates) - 10} more" if len(candidates) > 10 else ""
        lines.append(f"For clinician medication review: {shown}{more}.")
    others = [f"{counts[state]} {label}" for state, label in _STATE_LABEL.items() if state != "review_candidate" and counts.get(state)]
    if others:
        lines.append("Not listed as gaps: " + "; ".join(others) + ".")
    lines.append("This is a review list, not a prescription; the clinician decides.")
    event = {"type": "sglt2_care_gap", "rule_version": result["rule_version"], "counts": counts,
             "records": [{"patient_reference": row["patient_reference"], "organisation": row["organisation"], "state": row["state"],
                          "criteria": [c["condition"] if c["criterion"] == "ascvd" else f"eGFR {c['egfr']:g}" for c in row["criteria_met"]]}
                         for row in result["records"]]}
    return " ".join(lines), [event]


def _draft_reminders(db: Session, user: User, content: str) -> tuple[str, list[dict]]:
    if user.role not in {"administrator", "clinician"}:
        return "Only administrators and clinicians can ask me to draft patient reminders.", []
    reference = find_reference(content)
    if reference:
        patient = db.scalar(select(Patient).where(Patient.reference == reference))
        if not patient or (user.role == "clinician" and patient.organization_id != user.organization_id):
            return f"I cannot find {reference} in your authorised organisation.", []
        message = draft_message(db, user, patient)
        if message is None:
            return f"{reference} has opted out of email reminders, so I did not draft one.", [{"type": "outreach_skipped", "patient": reference}]
        detail = "with their recent results (consent recorded)" if message.includes_results else "without results (no consent to share results by email)"
        return (f"I drafted a ‘{message.template.replace('_', ' ')}’ email for {reference} {detail}. It is waiting for your approval in Patient Outreach; nothing has been sent.",
                [{"type": "outreach_draft", "message_id": message.id, "patient": reference}])
    result = draft_for_open_alerts(db, user)
    parts = [f"I drafted {len(result['drafted'])} reminder email(s) for patients with open review alerts"]
    if result["already_waiting"]:
        parts.append(f"{len(result['already_waiting'])} already had a draft waiting")
    if result["opted_out"]:
        parts.append(f"{len(result['opted_out'])} opted out of email and were skipped")
    answer = "; ".join(parts) + ". Each draft includes results only when the patient consented. Please review and approve them in Patient Outreach; nothing has been sent yet."
    return answer, [{"type": "outreach_batch", **result}]


def _crawl_public_source(url: str) -> dict:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_CRAWL_HOSTS:
        return {"error": "Crawler only permits HTTPS pages on the approved MOH and MEMS domains."}
    request = urllib.request.Request(url, headers={"User-Agent": "SentinelNetworkDemo/1.0 (synthetic-only reference crawler)"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(50_000).decode("utf-8", errors="replace")
            final_url = response.url
    except Exception as exc:
        return {"error": f"Crawl failed: {type(exc).__name__}"}
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(raw))).strip()
    return {"source_url": final_url, "content_sha256": hashlib.sha256(raw.encode()).hexdigest(), "excerpt": text[:800], "notice": "Reference-only crawl; clinician validation is required."}
