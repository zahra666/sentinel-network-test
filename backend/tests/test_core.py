import os

os.environ["DATABASE_URL"] = "sqlite:///./test_sentinel.db"
os.environ["APP_SECRET"] = "test-secret"

import pytest
from fastapi import HTTPException
from starlette.responses import Response
from sqlalchemy import func, select

from app.chain import verify_batch
from app.db import Base, SessionLocal, engine, initialise_database
from app.guideline_tree import assessment
from app.governance import build_governance_dashboard
from app.main import GovernanceReviewPayload, execute_scan, record_governance_review
from app.mcp_access import KeyApproval, approve_key, revoke_key, valid_key
from app.mdt import run_three_member_mdt
from app.models import AgentAccessKey, AuditEvent, GovernanceReview, Observation, Patient, Scan, User
from app.seed import seed
from app.security import require_roles
from app.services.sentinel_agent import advance_notification_workflows


def setup_module():
    Base.metadata.drop_all(engine)
    initialise_database()
    seed()


def test_seed_has_exact_care_mix():
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Patient)) == 100
        counts = dict(db.execute(select(Patient.care_setting, func.count()).group_by(Patient.care_setting)).all())
        assert counts == {"Medical Ward": 25, "O&G Ward": 12, "Outpatient Clinic": 45, "Surgical Ward": 18}


def test_scan_and_clinician_scope():
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.role == "administrator"))
        scan = execute_scan(db, trigger="test", actor=admin)
        db.commit()
        assert scan.status == "completed"
        assert db.scalar(select(func.count()).select_from(Scan)) == 1
        clinician = db.scalar(select(User).where(User.email == "clinician.aster@sentinel.demo"))
        assert db.scalar(select(func.count()).select_from(Patient).where(Patient.organization_id == clinician.organization_id)) == 34


def test_cpg_scope_and_local_tamper_verification():
    with SessionLocal() as db:
        pregnancy_record = db.scalar(select(Patient).where(Patient.pregnancy_related.is_(True)))
        result = assessment(pregnancy_record, list(pregnancy_record.observations))
        assert result["status"] == "out_of_scope"
        from app.models import SourceBatch
        batch = db.scalar(select(SourceBatch))
        baseline = verify_batch(batch, list(db.scalars(select(Observation)).all()))
        assert baseline["local_match"] is True
        observation = db.get(Observation, batch.manifest[0]["id"])
        observation.value = float(observation.value or 0) + 1
        db.commit()
        tampered = verify_batch(batch, list(db.scalars(select(Observation)).all()))
        assert tampered["local_match"] is False
        observation.value = batch.manifest[0]["value"]
        db.commit()


def test_mdt_contains_exactly_three_members():
    with SessionLocal() as db:
        patient = db.scalar(select(Patient))
        assert len(run_three_member_mdt(db, patient)["members"]) == 3


def test_sentinel_reminders_are_idempotent_and_in_app_only():
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.role == "administrator"))
        execute_scan(db, trigger="test-sentinel", actor=admin)
        db.commit()
        first = advance_notification_workflows(db)
        db.commit()
        second = advance_notification_workflows(db)
        db.commit()
        assert first["reminders"] >= 0
        assert first["review_queue_escalations"] >= 0
        assert second == {"reminders": 0, "review_queue_escalations": 0}


def test_governance_is_admin_only_and_records_review_evidence():
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == "admin@sentinel.demo"))
        clinician = db.scalar(select(User).where(User.email == "clinician.aster@sentinel.demo"))
        researcher = db.scalar(select(User).where(User.email == "researcher.approved@sentinel.demo"))
        guard = require_roles("administrator")
        with pytest.raises(HTTPException, match="Role is not authorised"):
            guard(clinician)
        with pytest.raises(HTTPException, match="Role is not authorised"):
            guard(researcher)

        payload = build_governance_dashboard(db)
        assert payload["framework"]["primary"] == "ISO/IEC 42001:2023"
        assert payload["llm_profile"]["integration_status"] == "planned"
        assert payload["llm_profile"]["is_configured"] is False
        assert "Structured output validation" in " ".join(payload["llm_profile"]["required_controls"])
        assert payload["live_evidence"]["llm_runtime"]["status"] == "planned / not configured"
        assert payload["live_evidence"]["source_batch"]["receipt_status"] in {"pending", "confirmed", "unavailable"}

        recorded = record_governance_review(GovernanceReviewPayload(note="Synthetic governance review recorded for the demo."), admin, db)
        review = recorded["latest_review"]
        assert review["note"] == "Synthetic governance review recorded for the demo."
        assert review["status"] == "not_due"
        assert review["evidence_snapshot"]["llm_runtime"]["configured"] is False

        assert db.scalar(select(func.count()).select_from(GovernanceReview)) >= 1
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "governance.review_attested").order_by(AuditEvent.created_at.desc()))
        assert event is not None
        assert event.detail["llm_status"] == "planned / not configured"


def test_mcp_key_is_one_time_scoped_and_revocable():
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.role == "administrator"))
        clinician = db.scalar(select(User).where(User.email == "clinician.aster@sentinel.demo"))
        approved_patient = db.scalar(select(Patient).where(Patient.organization_id == clinician.organization_id))
        issued = approve_key(
            KeyApproval(name="Synthetic reviewer", purpose="Read one synthetic evidence record", user_id=clinician.id,
                        scopes=["status:read", "patient:read"], patient_ids=[approved_patient.id], expires_in_minutes=30),
            Response(), admin, db,
        )
        key = db.get(AgentAccessKey, issued["id"])
        assert issued["api_key"].startswith("snt_mcp_")
        assert key.token_hash != issued["api_key"]
        assert valid_key(db, key).id == clinician.id
        revoke_key(key.id, admin, db)
        with pytest.raises(HTTPException, match="Invalid or expired"):
            valid_key(db, key)

        outside_patient = db.scalar(select(Patient).where(Patient.organization_id != clinician.organization_id))
        with pytest.raises(HTTPException, match="(?i)clinical access"):
            approve_key(
                KeyApproval(name="Invalid scope", purpose="Must not cross hospital boundary", user_id=clinician.id,
                            scopes=["patient:read"], patient_ids=[outside_patient.id], expires_in_minutes=30),
                Response(), admin, db,
            )
