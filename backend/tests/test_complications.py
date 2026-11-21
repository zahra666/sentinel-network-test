import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_sentinel.db")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ["SEED_PATIENT_COUNT"] = "100"

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.complications import EGFR_THRESHOLD, build_radar, care_gap_summary, complication_radar, kidney_trend, seed_care_history, stage_action
from app.chain import commitment_for, manifest_for
from app.db import Base, SessionLocal, engine, initialise_database
from app.models import AuditEvent, CareEvent, Observation, Patient, PatientMessage, SourceBatch, User
from app.security import require_roles
from app.seed import ANCHOR, seed


def setup_module():
    Base.metadata.drop_all(engine)
    initialise_database()
    seed()


def _user(db, email):
    return db.scalar(select(User).where(User.email == email))


def _patient(db, reference):
    return db.scalar(select(Patient).where(Patient.reference == reference))


def test_seed_is_idempotent_and_outside_the_source_batch():
    with SessionLocal() as db:
        count = db.scalar(select(func.count(CareEvent.id)))
        assert count > 0
        assert seed_care_history(db, ANCHOR) == 0
        assert db.scalar(select(func.count(CareEvent.id))) == count
        batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at))
        observations = list(db.scalars(select(Observation)).all())
        assert commitment_for(manifest_for(observations), batch.nonce) == batch.commitment


def test_radar_returns_four_domains_with_statuses():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        radar = complication_radar(_patient(db, "SYN-DM-003").id, admin, db)
        assert [domain["key"] for domain in radar["domains"]] == ["kidney", "eye", "foot", "heart"]
        for domain in radar["domains"]:
            assert domain["status"] in {"needs_review", "overdue", "not_recorded", "up_to_date"}
            assert (domain["action"] is None) == (domain["status"] == "up_to_date")
        assert radar["data_label"].startswith("Synthetic data")


def test_kidney_trend_projects_threshold_crossing_from_recorded_results():
    with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        found = None
        for patient in db.scalars(select(Patient).order_by(Patient.reference)).all():
            observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id)).all())
            trend = kidney_trend([], observations, now)
            if trend and trend["months_to_threshold"] is not None:
                found = (trend, observations)
                break
        assert found, "at least one synthetic record should project an eGFR 60 crossing"
        trend, observations = found
        assert len(trend["points"]) >= 3
        renal = sorted((o.observed_at, o.normalised_value) for o in observations if o.clinical_key == "egfr_ckd_epi")
        assert [point["value"] for point in trend["points"]] == [round(value, 1) for _, value in renal]  # same values as the Renal profile tab
        assert trend["slope_per_year"] < 0
        assert 0 < trend["months_to_threshold"] <= 24
        values = [point["value"] for point in trend["projection"]]
        assert values == sorted(values, reverse=True)
        assert trend["threshold"] == EGFR_THRESHOLD


def test_overdue_and_abnormal_results_change_status():
    with SessionLocal() as db:
        patient = _patient(db, "SYN-DM-001")
        now = ANCHOR + timedelta(days=30)
        stale = [CareEvent(patient_id=patient.id, kind="retinal_exam", performed_at=ANCHOR - timedelta(days=500), result="no_retinopathy")]
        eye = build_radar(patient, stale, [], now)["domains"][1]
        assert eye["status"] == "overdue" and eye["headline"].startswith("Overdue by")
        flagged = [CareEvent(patient_id=patient.id, kind="foot_exam", performed_at=ANCHOR, result="reduced_sensation")]
        foot = build_radar(patient, flagged, [], now)["domains"][2]
        assert foot["status"] == "needs_review"
        assert build_radar(patient, [], [], now)["domains"][1]["status"] == "not_recorded"


def test_clinicians_only_see_their_organisation_and_researchers_are_denied():
    with SessionLocal() as db:
        aster = _user(db, "clinician.aster@sentinel.demo")
        other = db.scalar(select(Patient).where(Patient.organization_id != aster.organization_id))
        with pytest.raises(HTTPException) as denied:
            complication_radar(other.id, aster, db)
        assert denied.value.status_code == 404
        guard = require_roles("administrator", "clinician")
        for email in ("researcher.approved@sentinel.demo", "caio@sentinel.demo"):
            with pytest.raises(HTTPException):
                guard(_user(db, email))
        summary = care_gap_summary(aster, db)
        assert summary["patients"] == db.scalar(select(func.count(Patient.id)).where(Patient.organization_id == aster.organization_id))


def test_staging_only_audits_and_never_contacts_or_refers():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        patient = _patient(db, "SYN-DM-004")
        before = db.scalar(select(func.count(PatientMessage.id)))
        result = stage_action(patient.id, "eye", admin, db)
        assert result["status"] == "staged" and "Nothing has been sent" in result["message"]
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "care_gap.order_staged").order_by(AuditEvent.created_at.desc()))
        assert event.detail["submitted"] is False and event.detail["domain"] == "eye"
        assert db.scalar(select(func.count(PatientMessage.id))) == before
        with pytest.raises(HTTPException):
            stage_action(patient.id, "liver", admin, db)
