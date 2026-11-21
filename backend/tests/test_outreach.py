import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_sentinel.db")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ["SEED_PATIENT_COUNT"] = "100"

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.agent import chat
from app.db import Base, SessionLocal, engine, initialise_database
from app.models import AuditEvent, Patient, PatientContactConsent, PatientMessage, User
from app.outreach import ConsentPayload, approve, consent_for, discard, draft_message, outbox, record_consent, request_draft
from app.security import require_roles
from app.seed import seed
from app.main import execute_scan


def setup_module():
    Base.metadata.drop_all(engine)
    initialise_database()
    seed()
    with SessionLocal() as db:
        execute_scan(db, trigger="test", actor=_user(db, "admin@sentinel.demo"))
        db.commit()


def _user(db, email):
    return db.scalar(select(User).where(User.email == email))


def _patient_with(db, **consent):
    for patient in db.scalars(select(Patient).order_by(Patient.reference)).all():
        record = consent_for(db, patient)
        if all(getattr(record, key) == value for key, value in consent.items()):
            return patient
    raise AssertionError("no matching synthetic patient")


def test_outreach_is_staff_only():
    guard = require_roles("administrator", "clinician")
    with SessionLocal() as db:
        for email in ("researcher.approved@sentinel.demo", "caio@sentinel.demo"):
            with pytest.raises(HTTPException):
                guard(_user(db, email))


def test_draft_waits_for_approval_and_uses_reserved_domain():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        patient = _patient_with(db, email_reminders=True)
        message = draft_message(db, admin, patient)
        assert message.status == "awaiting_approval"
        assert message.recipient.endswith(".invalid")
        assert message.delivery == "simulated_outbox"
        assert draft_message(db, admin, patient).id == message.id  # no duplicate drafts
        db.commit()


def test_results_only_included_with_consent():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        with_consent = draft_message(db, admin, _patient_with(db, email_reminders=True, share_results=True))
        without = draft_message(db, admin, _patient_with(db, email_reminders=True, share_results=False))
        assert with_consent.includes_results and "HbA1c" in with_consent.body
        assert not without.includes_results and "HbA1c" not in without.body
        db.commit()


def test_opted_out_patient_gets_no_draft():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        patient = _patient_with(db, email_reminders=True)
        record_consent(patient.id, ConsentPayload(email_reminders=False, share_results=True), admin, db)
        consent = db.scalar(select(PatientContactConsent).where(PatientContactConsent.patient_id == patient.id))
        assert consent.share_results is False
        for pending in db.scalars(select(PatientMessage).where(PatientMessage.patient_id == patient.id, PatientMessage.status == "awaiting_approval")).all():
            discard(pending.id, admin, db)
        with pytest.raises(HTTPException) as error:
            request_draft(patient.id, admin, db)
        assert error.value.status_code == 409


def test_approve_records_staff_and_audit():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        patient = _patient_with(db, email_reminders=True)
        record_consent(patient.id, ConsentPayload(email_reminders=True, share_results=False), admin, db)
        message = request_draft(patient.id, admin, db)
        sent = approve(message["id"], admin, db)
        assert sent["status"] == "sent" and sent["approved_by"] == "admin@sentinel.demo"
        with pytest.raises(HTTPException):
            approve(message["id"], admin, db)
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "outreach.sent_simulated", AuditEvent.entity_id == message["id"]))


def test_clinician_cannot_reach_other_hospital():
    with SessionLocal() as db:
        clinician = _user(db, "clinician.aster@sentinel.demo")
        other = db.scalar(select(Patient).where(Patient.organization_id != clinician.organization_id))
        with pytest.raises(HTTPException) as error:
            request_draft(other.id, clinician, db)
        assert error.value.status_code == 404
        listed = outbox(clinician, db)["messages"]
        assert all(db.get(Patient, item["patient_id"]).organization_id == clinician.organization_id for item in listed)


def test_agent_drafts_batch_for_open_alerts_without_sending():
    with SessionLocal() as db:
        clinician = _user(db, "clinician.aster@sentinel.demo")
        reply = chat(db, clinician, "Please email patients with open alerts")
        db.commit()
        event = reply["tool_events"][0]
        assert event["type"] == "outreach_batch"
        assert "nothing has been sent" in reply["message"]
        sent_by_agent = db.scalars(select(PatientMessage).where(PatientMessage.requested_by == clinician.id, PatientMessage.status == "sent")).all()
        assert not sent_by_agent


def test_agent_drafts_for_named_patient():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        patient = _patient_with(db, email_reminders=True)
        reply = chat(db, admin, f"remind {patient.reference.lower()} about follow-up")
        db.commit()
        assert reply["tool_events"][0]["patient"] == patient.reference


def test_withdrawing_consent_discards_draft_with_results():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        patient = _patient_with(db, email_reminders=True, share_results=True)
        message = draft_message(db, admin, patient)
        db.commit()
        assert message.includes_results
        record_consent(patient.id, ConsentPayload(email_reminders=True, share_results=False), admin, db)
        db.refresh(message)
        assert message.status == "discarded"
