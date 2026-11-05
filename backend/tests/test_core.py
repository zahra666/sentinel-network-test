import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
from app.main import GovernanceReviewPayload, LoginPayload, McpDemoSessionPayload, execute_scan, record_governance_review, scheduled_scan, start_mcp_demo_session
from app.mcp_access import KeyApproval, approve_key, revoke_key, valid_key
from app.mdt import run_three_member_mdt
from app.models import AgentAccessKey, AuditEvent, GovernanceReview, McpDemoSession, Observation, Patient, Scan, SourceBatch, User
from app.seed import seed
from app.security import require_roles, verify_password
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


def test_synthetic_demo_accounts_use_documented_password():
    assert LoginPayload(email="admin@sentinel.demo", password="12345").password == "12345"
    with SessionLocal() as db:
        users = list(db.scalars(select(User).where(User.email.in_((
            "admin@sentinel.demo", "caio@sentinel.demo", "clinician.aster@sentinel.demo",
            "clinician.beryl@sentinel.demo", "clinician.cobalt@sentinel.demo",
            "researcher.approved@sentinel.demo", "researcher.denied@sentinel.demo",
        )))).all())
        assert len(users) == 7
        assert all(verify_password("12345", user.password_hash) for user in users)


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


def test_governance_is_caio_or_admin_only_and_records_review_evidence():
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == "admin@sentinel.demo"))
        caio = db.scalar(select(User).where(User.email == "caio@sentinel.demo"))
        clinician = db.scalar(select(User).where(User.email == "clinician.aster@sentinel.demo"))
        researcher = db.scalar(select(User).where(User.email == "researcher.approved@sentinel.demo"))
        guard = require_roles("administrator", "caio")
        with pytest.raises(HTTPException, match="Role is not authorised"):
            guard(clinician)
        with pytest.raises(HTTPException, match="Role is not authorised"):
            guard(researcher)
        assert guard(caio).id == caio.id

        payload = build_governance_dashboard(db)
        assert payload["framework"]["primary"] == "ISO/IEC 42001:2023"
        assert payload["llm_profile"]["integration_status"] == "planned"
        assert payload["llm_profile"]["is_configured"] is False
        assert "Structured output validation" in " ".join(payload["llm_profile"]["required_controls"])
        assert payload["live_evidence"]["llm_runtime"]["status"] == "planned / not configured"
        assert payload["live_evidence"]["source_batch"]["receipt_status"] in {"pending", "confirmed", "unavailable"}
        assert payload["live_evidence"]["mcp_activity"]["observed_agents_last_five_minutes"] >= 0
        assert "token" not in str(payload["live_evidence"]["mcp_activity"]).lower()

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
        issued = approve_key(
            KeyApproval(name="Synthetic reviewer", purpose="Read organisation-scoped synthetic evidence", user_id=clinician.id,
                        scopes=["status:read", "clinical:read:organisation"], expires_in_minutes=30),
            Response(), admin, db,
        )
        key = db.get(AgentAccessKey, issued["id"])
        assert issued["api_key"].startswith("snt_mcp_")
        assert key.token_hash != issued["api_key"]
        assert key.patient_ids == []
        assert valid_key(db, key).id == clinician.id
        revoke_key(key.id, admin, db)
        with pytest.raises(HTTPException, match="Invalid or expired"):
            valid_key(db, key)

        with pytest.raises(HTTPException, match="(?i)scope"):
            approve_key(
                KeyApproval(name="Invalid scope", purpose="Must not cross hospital boundary", user_id=clinician.id,
                            scopes=["clinical:read:system"], expires_in_minutes=30),
                Response(), admin, db,
            )


def test_mcp_governance_simulation_is_time_bounded_and_audited():
    with SessionLocal() as db:
        caio = db.scalar(select(User).where(User.email == "caio@sentinel.demo"))
        result = start_mcp_demo_session(McpDemoSessionPayload(label="Synthetic external agent"), caio, db)
        assert result["status"] == "simulated"
        assert result["dashboard"]["live_evidence"]["mcp_activity"]["active_simulated_sessions"] >= 1
        session = db.get(McpDemoSession, result["id"])
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert build_governance_dashboard(db)["live_evidence"]["mcp_activity"]["active_simulated_sessions"] == 0
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "mcp.demo_session_started"))
        assert event is not None
        assert event.detail == {"simulation": True, "expires_in_minutes": 5}


def test_github_scheduler_requires_shared_secret(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "get_settings", lambda: SimpleNamespace(
        schedule_enabled=True, scheduler_trigger_token="test-trigger", scheduler_mode="github_actions",
    ))
    with SessionLocal() as db:
        with pytest.raises(HTTPException, match="Invalid scheduler trigger"):
            scheduled_scan("Bearer wrong", db)
        result = scheduled_scan("Bearer test-trigger", db)
        assert result["status"] == "completed"
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "scheduler.github_triggered").order_by(AuditEvent.created_at.desc()))
        assert event is not None and event.detail["mode"] == "github_actions"


def test_existing_cloud_seed_bootstraps_its_missing_source_batch():
    """The cloud SQL bootstrap can predate the first FastAPI process start."""
    with SessionLocal() as db:
        db.query(SourceBatch).delete()
        db.commit()
    seed()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SourceBatch)) == 1
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "seed.source_batch_completed"))
        assert event is not None
