"""Patient outreach: Goola Agent drafts reminder emails, staff approve them.

Safety boundaries for the synthetic demo:
- The agent only drafts. Nothing is "sent" until an administrator or clinician approves it.
- Delivery is a simulated outbox. No SMTP or messaging gateway is called, and every
  synthetic address uses the reserved ``.invalid`` domain, so it cannot reach a real inbox.
- Results are included only when the patient's recorded consent allows it; otherwise
  the email is a plain reminder with no health data.
- Messages contain reminders and recorded results only, never a diagnosis or treatment advice.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .guideline_tree import assessment
from .models import Alert, AuditEvent, Observation, Organization, Patient, PatientContactConsent, PatientMessage, User
from .security import require_roles

router = APIRouter(prefix="/api/outreach", tags=["outreach"])
staff = require_roles("administrator", "clinician")

PENDING = "awaiting_approval"
REFERENCE_PATTERN = re.compile(r"\bSYN-DM-\d{3,5}\b", re.IGNORECASE)
MAX_BATCH = 10


class ConsentPayload(BaseModel):
    email_reminders: bool
    share_results: bool


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def can_access(user: User, patient: Patient | None) -> bool:
    return bool(patient) and user.role in {"administrator", "clinician"} and (user.role == "administrator" or user.organization_id == patient.organization_id)


def consent_for(db: Session, patient: Patient) -> PatientContactConsent:
    """Return the recorded preference, creating a deterministic synthetic one on first use."""
    consent = db.scalar(select(PatientContactConsent).where(PatientContactConsent.patient_id == patient.id))
    if consent:
        return consent
    bucket = int(hashlib.sha256(patient.reference.encode()).hexdigest(), 16) % 10
    consent = PatientContactConsent(
        patient_id=patient.id,
        contact_email=f"{patient.reference.lower()}@patient.synthetic.invalid",
        email_reminders=bucket != 0,  # about 1 in 10 synthetic patients opted out of email
        share_results=bucket < 6,  # about half agreed to receive results by email
    )
    db.add(consent)
    db.flush()
    return consent


def _latest(observations: list[Observation], *keys: str) -> Observation | None:
    matches = [item for item in observations if item.clinical_key in keys and item.normalised_value is not None]
    return max(matches, key=lambda item: item.observed_at) if matches else None


def _compose(patient: Patient, organization: Organization | None, observations: list[Observation], share_results: bool) -> tuple[str, str, str, bool]:
    status = assessment(patient, observations)["status"]
    hba1c = _latest(observations, "hba1c_ngsp", "hba1c_ifcc")
    fpg = _latest(observations, "fpg")
    clinic = organization.name if organization else "your clinic"
    last_test = max((_as_utc(item.observed_at) for item in observations), default=None)
    days_since = (datetime.now(timezone.utc) - last_test).days if last_test else None

    if status in {"data_quality_review", "confirmation_gap"}:
        template = "repeat_test"
        subject = "Peringatan susulan diabetes · Please book a repeat blood test"
        reason = "Your care team needs one more blood test to complete your review."
    elif status == "evidence_requires_clinician_review":
        template = "review_appointment"
        subject = "Peringatan susulan diabetes · Your care team would like to see you"
        reason = "Your care team has looked at your recent results and would like to arrange a follow-up appointment."
    else:
        template = "routine_follow_up"
        subject = "Peringatan susulan diabetes · Time for your diabetes check-up"
        reason = "It is time for your regular diabetes check-up."
    if days_since is not None and days_since > 180:
        reason += f" Our records show your last test was about {days_since // 30} months ago."

    lines = ["Dear patient,", "", reason, f"Please contact {clinic} to book a convenient time.", ""]
    included = False
    if share_results and (hba1c or fpg):
        included = True
        lines.append("You agreed to receive your recent results by email:")
        if hba1c:
            lines.append(f"  • HbA1c {hba1c.normalised_value:.1f}{hba1c.normalised_ucum_unit or '%'} ({_as_utc(hba1c.observed_at):%d %b %Y})")
        if fpg:
            lines.append(f"  • Fasting plasma glucose {fpg.normalised_value:.1f} {fpg.normalised_ucum_unit or 'mmol/L'} ({_as_utc(fpg.observed_at):%d %b %Y})")
        lines.append("Your doctor will explain what these results mean for you at your visit.")
    else:
        lines.append("Your results are available at your visit. They are not included here because you have not agreed to receive results by email.")
    lines += ["", f"— {clinic} diabetes care team", "", "This reminder was drafted by Goola Agent and approved by a member of your care team.",
              "Synthetic data — demonstration only."]
    return template, subject, "\n".join(lines), included


def draft_message(db: Session, user: User, patient: Patient) -> PatientMessage | None:
    """Agent tool: draft one reminder. Returns None when the patient opted out or a draft is already waiting."""
    if not can_access(user, patient):
        raise PermissionError("Patient is outside your authorised organisation")
    consent = consent_for(db, patient)
    if not consent.email_reminders:
        return None
    existing = db.scalar(select(PatientMessage).where(PatientMessage.patient_id == patient.id, PatientMessage.status == PENDING))
    if existing:
        return existing
    observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at)).all())
    template, subject, body, included = _compose(patient, db.get(Organization, patient.organization_id), observations, consent.share_results)
    message = PatientMessage(patient_id=patient.id, organization_id=patient.organization_id, template=template, recipient=consent.contact_email,
                             subject=subject, body=body, includes_results=included, requested_by=user.id)
    db.add(message)
    db.flush()
    db.add(AuditEvent(actor_id=user.id, event_type="outreach.draft_created", entity_type="patient_message", entity_id=message.id,
                      detail={"template": template, "includes_results": included, "drafted_by": "sentinel_agent", "requires_staff_approval": True}))
    return message


def draft_for_open_alerts(db: Session, user: User, limit: int = MAX_BATCH) -> dict:
    """Agent tool: draft reminders for patients with unacknowledged review alerts."""
    query = select(Patient).join(Alert, Alert.patient_id == Patient.id).where(Alert.status == "new").distinct().order_by(Patient.reference)
    if user.role == "clinician":
        query = query.where(Patient.organization_id == user.organization_id)
    drafted, opted_out, already = [], [], []
    for patient in db.scalars(query).all():
        if len(drafted) >= limit:
            break
        before = db.scalar(select(PatientMessage.id).where(PatientMessage.patient_id == patient.id, PatientMessage.status == PENDING))
        message = draft_message(db, user, patient)
        if message is None:
            opted_out.append(patient.reference)
        elif before:
            already.append(patient.reference)
        else:
            drafted.append(patient.reference)
    return {"drafted": drafted, "opted_out": opted_out, "already_waiting": already}


def find_reference(text: str) -> str | None:
    match = REFERENCE_PATTERN.search(text)
    return match.group(0).upper() if match else None


def serialize_message(message: PatientMessage, db: Session) -> dict:
    patient = db.get(Patient, message.patient_id)
    approver = db.get(User, message.approved_by) if message.approved_by else None
    return {"id": message.id, "patient_id": message.patient_id, "patient_reference": patient.reference if patient else None,
            "channel": message.channel, "template": message.template, "recipient": message.recipient, "subject": message.subject,
            "body": message.body, "includes_results": message.includes_results, "status": message.status,
            "drafted_by": message.drafted_by_agent, "approved_by": approver.email if approver else None,
            "approved_at": message.approved_at.isoformat() if message.approved_at else None, "delivery": message.delivery,
            "created_at": message.created_at.isoformat(), "data_label": "Synthetic data — demonstration only"}


def serialize_consent(consent: PatientContactConsent) -> dict:
    return {"contact_email": consent.contact_email, "email_reminders": consent.email_reminders, "share_results": consent.share_results,
            "recorded_at": consent.recorded_at.isoformat() if consent.recorded_at else None}


def _patient(db: Session, user: User, patient_id: str) -> Patient:
    patient = db.get(Patient, patient_id)
    if not can_access(user, patient):
        raise HTTPException(status_code=404, detail="Patient record not found")
    return patient


def _message(db: Session, user: User, message_id: str) -> PatientMessage:
    message = db.get(PatientMessage, message_id)
    if not message or not can_access(user, db.get(Patient, message.patient_id)):
        raise HTTPException(status_code=404, detail="Message not found")
    return message


@router.get("/patients/{patient_id}")
def patient_outreach(patient_id: str, user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    patient = _patient(db, user, patient_id)
    consent = consent_for(db, patient)
    messages = db.scalars(select(PatientMessage).where(PatientMessage.patient_id == patient.id).order_by(PatientMessage.created_at.desc())).all()
    db.commit()
    return {"consent": serialize_consent(consent), "messages": [serialize_message(item, db) for item in messages]}


@router.post("/patients/{patient_id}/consent")
def record_consent(patient_id: str, payload: ConsentPayload, user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    patient = _patient(db, user, patient_id)
    consent = consent_for(db, patient)
    consent.email_reminders = payload.email_reminders
    consent.share_results = payload.share_results and payload.email_reminders
    consent.recorded_by = user.id
    consent.recorded_at = datetime.now(timezone.utc)
    for pending in db.scalars(select(PatientMessage).where(PatientMessage.patient_id == patient.id, PatientMessage.status == PENDING)).all():
        if not consent.email_reminders or (pending.includes_results and not consent.share_results):
            pending.status = "discarded"  # a draft written under the old consent must not be sent
    db.add(AuditEvent(actor_id=user.id, event_type="outreach.consent_recorded", entity_type="patient", entity_id=patient.id,
                      detail={"email_reminders": consent.email_reminders, "share_results": consent.share_results}))
    db.commit()
    return serialize_consent(consent)


@router.post("/patients/{patient_id}/draft")
def request_draft(patient_id: str, user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    patient = _patient(db, user, patient_id)
    message = draft_message(db, user, patient)
    if message is None:
        db.commit()
        raise HTTPException(status_code=409, detail="This patient has opted out of email reminders.")
    db.commit()
    return serialize_message(message, db)


@router.post("/drafts/open-alerts")
def request_batch(user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    result = draft_for_open_alerts(db, user)
    db.commit()
    return result


@router.get("/outbox")
def outbox(user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    query = select(PatientMessage).order_by(PatientMessage.created_at.desc()).limit(100)
    if user.role == "clinician":
        query = query.where(PatientMessage.organization_id == user.organization_id)
    messages = [serialize_message(item, db) for item in db.scalars(query).all()]
    return {"messages": messages, "awaiting_approval": sum(item["status"] == PENDING for item in messages),
            "sent": sum(item["status"] == "sent" for item in messages), "delivery": "simulated_outbox"}


@router.post("/messages/{message_id}/approve")
def approve(message_id: str, user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    message = _message(db, user, message_id)
    if message.status != PENDING:
        raise HTTPException(status_code=409, detail="Only drafts awaiting approval can be sent.")
    message.status = "sent"
    message.approved_by = user.id
    message.approved_at = datetime.now(timezone.utc)
    db.add(AuditEvent(actor_id=user.id, event_type="outreach.sent_simulated", entity_type="patient_message", entity_id=message.id,
                      detail={"delivery": "simulated_outbox", "includes_results": message.includes_results}))
    db.commit()
    return serialize_message(message, db)


@router.post("/messages/{message_id}/discard")
def discard(message_id: str, user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    message = _message(db, user, message_id)
    if message.status != PENDING:
        raise HTTPException(status_code=409, detail="Only drafts awaiting approval can be discarded.")
    message.status = "discarded"
    db.add(AuditEvent(actor_id=user.id, event_type="outreach.draft_discarded", entity_type="patient_message", entity_id=message.id, detail={}))
    db.commit()
    return serialize_message(message, db)
