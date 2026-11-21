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
from .guideline_tree import assessment
from .care_gap import care_gaps_for_user
from .outreach import draft_for_open_alerts, draft_message, find_reference
from .complications import build_radar
from .models import AgentConversation, AgentMessage, Alert, CareEvent, AuditEvent, DelegatedTask, Finding, Observation, Organization, Patient, SourceBatch, User

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
    words = set(re.findall(r"[a-z\u0600-\u06ff]+", text))
    events: list[dict] = []
    reference = find_reference(content)
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
    elif reference:
        answer = _patient_summary(db, user, reference)
    elif words & GREETINGS and len(words) <= 4:
        answer = "Hello. Ask me for today's review status, a patient summary such as ‘SYN-DM-442’, SGLT2 care gaps, or reminder drafts."
    elif words & THANKS and len(words) <= 5:
        answer = "You are welcome. Anything else you would like me to check?"
    elif words & HELP_WORDS or "what can you" in text:
        answer = CAPABILITIES
    elif words & STATUS_WORDS or "how many" in text:
        answer = _cohort_status(db, user)
    elif "patient" in text or "evidence" in text:
        answer = "For a patient summary, include the reference, for example ‘SYN-DM-442’. For a full evidence review, open the patient in Patients and choose ‘Delegate evidence review’. I enforce your organisation boundary and return only rule evidence and data-quality context."
    elif "regional" in text or "district" in text:
        answer = "Regional analysis is limited to authorised, fixed aggregate queries with denominator and small-cell controls. I can hand that to the aggregate-analysis subagent after grant verification."
    else:
        db.flush()
        previous = db.scalar(select(AgentMessage.content).where(AgentMessage.conversation_id == conversation.id, AgentMessage.role == "assistant").order_by(AgentMessage.created_at.desc()))
        options = [option for option in FALLBACKS if option != previous]
        answer = options[len(content) % len(options)]
    db.add(AgentMessage(conversation_id=conversation.id, role="assistant", content=answer, tool_events=events))
    db.add(AuditEvent(actor_id=user.id, event_type="agent.chat", entity_type="agent_conversation", entity_id=conversation.id, detail={"tool_events": events}))
    return {"conversation_id": conversation.id, "message": answer, "tool_events": events}


GREETINGS = {"hi", "hello", "hey", "salam", "assalamualaikum", "morning", "afternoon", "evening", "مرحبا", "هلا", "السلام"}
THANKS = {"thanks", "thank", "thx", "شكرا"}
HELP_WORDS = {"help", "capabilities", "commands"}
STATUS_WORDS = {"status", "summary", "overview", "today", "alerts", "queue", "cohort"}
CAPABILITIES = ("I can: give today's review status (‘status’); summarise one patient (‘SYN-DM-442’); count records with a hypertension diagnosis (‘how many patients have high blood pressure?’); list T2DM records with ASCVD or eGFR < 60 "
                "and no active SGLT2-inhibitor entry (‘SGLT2 care gaps’); draft reminder emails for staff approval (‘email patients with open alerts’); "
                "and verify source integrity (‘verify integrity’). I do not diagnose, prescribe, or access data outside your role.")
FALLBACKS = (
    "I did not recognise that request. Try ‘status’, a patient reference such as ‘SYN-DM-442’, or ‘SGLT2 care gaps’.",
    "I work from a fixed, audited set of tasks rather than free conversation. Type ‘help’ to see them.",
    "That is outside what I can do. I can summarise a patient, report today's review status, draft reminders or verify integrity.",
)
TEST_NAMES = {"hba1c_ngsp": "HbA1c", "hba1c_ifcc": "HbA1c (IFCC)", "fpg": "fasting glucose", "rpg": "random glucose", "egfr_ckd_epi": "eGFR"}
UNIT_NAMES = {"mL/min/{1.73_m2}": "mL/min/1.73m\u00b2"}
STATUS_NAMES = {"evidence_requires_clinician_review": "clinician review required", "confirmation_gap": "confirmation gap",
                "data_quality_review": "data-quality review", "no_qualifying_evidence": "no qualifying evidence", "out_of_scope_pregnancy": "out of scope (pregnancy)"}


def _unit(code: str | None) -> str:
    if not code:
        return ""
    return "%" if code == "%" else " " + UNIT_NAMES.get(code, code)


def _staff_patients(user: User):
    query = select(Patient)
    if user.role == "clinician":
        query = query.where(Patient.organization_id == user.organization_id)
    return query


def _cohort_status(db: Session, user: User) -> str:
    if user.role not in {"administrator", "clinician"}:
        return "Review status is available to administrators and clinicians. Researchers can request fixed regional aggregates instead."
    patient_ids = list(db.scalars(_staff_patients(user).with_only_columns(Patient.id)).all())
    if not patient_ids:
        return "There are no patients in your authorised scope yet."
    alerts = list(db.scalars(select(Alert).where(Alert.patient_id.in_(patient_ids))).all())
    open_alerts = [alert for alert in alerts if alert.status == "new"]
    counts: dict[str, int] = {}
    for alert in open_alerts:
        finding = db.get(Finding, alert.finding_id)
        if finding:
            counts[finding.status] = counts.get(finding.status, 0) + 1
    scope = "your hospital" if user.role == "clinician" else "all three synthetic hospitals"
    if not open_alerts:
        return f"{len(patient_ids)} patients are monitored in {scope} and there are no open review alerts. Run a scan from Overview to evaluate the latest results."
    detail = ", ".join(f"{count} {STATUS_NAMES.get(status, status.replace('_', ' '))}" for status, count in sorted(counts.items(), key=lambda item: -item[1]))
    return (f"{len(patient_ids)} patients are monitored in {scope}. {len(open_alerts)} review alerts are open: {detail}. "
            f"{sum(alert.status == 'acknowledged' for alert in alerts)} have been acknowledged. Open Review Queue to work through them.")


def _patient_summary(db: Session, user: User, reference: str) -> str:
    if user.role not in {"administrator", "clinician"}:
        return "Patient-level summaries are available to administrators and clinicians only."
    patient = db.scalar(_staff_patients(user).where(Patient.reference == reference))
    if not patient:
        return f"I cannot find {reference} in your authorised organisation."
    organisation = db.get(Organization, patient.organization_id)
    observations = db.scalars(select(Observation).where(Observation.patient_id == patient.id, Observation.clinical_key.in_(list(TEST_NAMES)),
                                                        Observation.normalised_value.is_not(None)).order_by(Observation.observed_at.desc())).all()
    latest: dict[str, Observation] = {}
    for item in observations:
        latest.setdefault(item.clinical_key, item)
    results = "; ".join(f"{TEST_NAMES[key]} {latest[key].normalised_value:g}{_unit(latest[key].normalised_ucum_unit)}"
                        + f" ({latest[key].observed_at:%d %b %Y})" for key in TEST_NAMES if key in latest)
    observations_all = db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at)).all()
    status = assessment(patient, observations_all)["status"]
    state = STATUS_NAMES.get(status, status.replace("_", " "))
    finding = db.scalar(select(Finding).where(Finding.patient_id == patient.id).order_by(Finding.created_at.desc()))
    alert = db.scalar(select(Alert).where(Alert.finding_id == finding.id)) if finding else None
    alert_text = f" Review alert: {alert.status}." if alert else ""
    where = f" ({organisation.name}, {patient.care_setting})" if organisation else ""
    radar = build_radar(patient, list(db.scalars(select(CareEvent).where(CareEvent.patient_id == patient.id)).all()), list(observations_all))
    flagged = [f"{domain['label'].lower()}: {domain['headline'][0].lower() + domain['headline'][1:]}" for domain in radar["domains"] if domain["status"] != "up_to_date"]
    radar_text = f" Complication screening: {'; '.join(flagged)}." if flagged else " Complication screening is up to date."
    return (f"{patient.reference}{where}. Latest results: {results or 'none recorded'}. Review state: {state}.{alert_text}{radar_text} "
            "Open the patient in Patients for the full record. This is a summary for review, not a diagnosis.")


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
