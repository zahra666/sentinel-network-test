"""SGLT2 medication-list review: criteria, safety states and access boundaries."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_sentinel.db")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ["SEED_PATIENT_COUNT"] = "100"

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import mcp_server
from app.agent import chat
from app.care_gap import care_gaps_for_user, evaluate_patient, sglt2_care_gaps
from app.db import Base, get_db
from app.main import app
from app.models import AgentAccessKey, AuditEvent, Condition, MedicationStatement, Observation, Organization, Patient, User
from app.security import create_token
from app.seed import synthetic_ascvd_code, synthetic_egfr, synthetic_medications
from app.terminology import ATC_SYSTEM, LOINC_SYSTEM, LOINC_VERSION, SNOMED_SYSTEM, map_measurement

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def condition(patient, code, suffix, display="demo"):
    return Condition(patient_id=patient.id, source_id=f"C-{patient.reference}-{suffix}", clinical_status="active", verification_status="confirmed",
                     source_coding_system=SNOMED_SYSTEM, source_code=code, snomed_code=code, snomed_display=display, terminology_status="verified")


def egfr(patient, value, *, suffix="1", local="EGFR_CKD_EPI", loinc="62238-1", days_ago=5):
    mapping = map_measurement(system=LOINC_SYSTEM, code=loinc, display=None, version=LOINC_VERSION, original_code=local, value=value, ucum_unit="mL/min/{1.73_m2}")
    return Observation(patient_id=patient.id, code="egfr", value=value, unit="mL/min/{1.73_m2}", observed_at=NOW - timedelta(days=days_ago),
                       source_id=f"E-{patient.reference}-{suffix}", ucum_unit="mL/min/{1.73_m2}", egfr_equation="CKD-EPI",
                       **{key: mapping[key] for key in ("original_code", "coding_system", "standard_code", "coding_display", "terminology_version", "clinical_key", "terminology_status", "normalised_value", "normalised_ucum_unit")})


def medication(patient, atc, *, status="active", suffix="1"):
    verified = atc in {"A10BA02", "A10BK01", "A10BK03"}
    return MedicationStatement(patient_id=patient.id, source_id=f"M-{patient.reference}-{suffix}", status=status,
                               source_coding_system=ATC_SYSTEM if verified else "urn:local", source_code=atc,
                               atc_code=atc if verified else None, atc_display=atc if verified else None,
                               terminology_status="verified" if verified else "needs_terminology_review")


@pytest.fixture
def cohort(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        a = Organization(code="A", name="Synthetic A", district="D1")
        b = Organization(code="B", name="Synthetic B", district="D2")
        db.add_all([a, b]); db.flush()
        admin = User(email="admin@t.demo", password_hash="x", role="administrator")
        clinician = User(email="clin@t.demo", password_hash="x", role="clinician", organization_id=a.id)
        caio = User(email="caio@t.demo", password_hash="x", role="caio")
        researcher = User(email="res@t.demo", password_hash="x", role="researcher")
        db.add_all([admin, clinician, caio, researcher]); db.flush()
        p = {name: Patient(reference=name, organization_id=(b if name == "OTHER-ORG" else a).id, care_setting="Outpatient Clinic",
                           age_group="45-64", pregnancy_related=name == "PREGNANT")
             for name in ("ASCVD-GAP", "CKD-GAP", "ON-SGLT2", "STOPPED", "UNMAPPED", "LOW-EGFR", "NO-EGFR-ASCVD", "HEALTHY", "PREGNANT", "OTHER-ORG", "CORRECTED")}
        db.add_all(p.values()); db.flush()
        for patient in p.values():
            db.add(condition(patient, "44054006", "t2dm"))
        db.add_all([
            condition(p["ASCVD-GAP"], "22298006", "mi"), egfr(p["ASCVD-GAP"], 75), medication(p["ASCVD-GAP"], "A10BA02"),
            egfr(p["CKD-GAP"], 45),
            condition(p["ON-SGLT2"], "230690007", "cva"), egfr(p["ON-SGLT2"], 50), medication(p["ON-SGLT2"], "A10BK03"),
            condition(p["STOPPED"], "399957001", "pad"), egfr(p["STOPPED"], 70), medication(p["STOPPED"], "A10BK01", status="stopped"),
            egfr(p["UNMAPPED"], 40), medication(p["UNMAPPED"], "LOCAL-0417"),
            egfr(p["LOW-EGFR"], 17),
            condition(p["NO-EGFR-ASCVD"], "22298006", "mi"),
            egfr(p["HEALTHY"], 88),
            condition(p["PREGNANT"], "22298006", "mi"), egfr(p["PREGNANT"], 50),
            egfr(p["OTHER-ORG"], 30),
            # A corrected result supersedes the earlier version of the same source record.
            egfr(p["CORRECTED"], 45, suffix="x"),
        ])
        db.flush()
        corrected = egfr(p["CORRECTED"], 82, suffix="x")
        corrected.version = 2
        db.add(corrected)
        db.commit()

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    client = TestClient(app)
    yield SimpleNamespace(factory=factory, client=client, a=a, b=b, admin=admin, clinician=clinician, caio=caio, researcher=researcher, p=p)
    app.dependency_overrides.pop(get_db, None)
    client.close()
    engine.dispose()


def states(result):
    return {row["patient_reference"]: row["state"] for row in result["records"]}


def test_criteria_and_safety_states(cohort):
    with cohort.factory() as db:
        result = sglt2_care_gaps(db, None, 50)
    listed = states(result)
    assert listed["ASCVD-GAP"] == "review_candidate"
    assert listed["CKD-GAP"] == "review_candidate"
    assert listed["STOPPED"] == "review_candidate"            # a stopped entry is not current use
    assert listed["OTHER-ORG"] == "review_candidate"
    assert listed["UNMAPPED"] == "medication_list_needs_review"  # never assume "not on" from an unmapped code
    assert listed["LOW-EGFR"] == "below_initiation_threshold"
    assert listed["NO-EGFR-ASCVD"] == "insufficient_evidence"
    for covered in ("ON-SGLT2", "HEALTHY", "PREGNANT", "CORRECTED"):
        assert covered not in listed
    assert result["counts"]["on_sglt2_inhibitor"] == 1
    assert result["counts"]["out_of_scope"] == 1
    assert result["counts"]["criterion_not_met"] == 2           # HEALTHY and the corrected (v2 = 82) record
    assert "not a prescription" in result["notice"]


def test_incompatible_egfr_is_not_compared(cohort):
    with cohort.factory() as db:
        patient = db.scalar(select(Patient).where(Patient.reference == "HEALTHY"))
        ambiguous = egfr(patient, 40, suffix="amb", local="HBA1C")  # local code disagrees with the LOINC meaning
        assert ambiguous.terminology_status == "ambiguous_local_mapping"
        result = evaluate_patient(patient, list(patient.conditions), [ambiguous], [])
    assert result["state"] == "insufficient_evidence"


def test_clinician_is_bounded_to_own_hospital_and_audited(cohort):
    with cohort.factory() as db:
        clinician = db.get(User, cohort.clinician.id)
        result = care_gaps_for_user(db, clinician, 50)
        db.commit()
        assert "OTHER-ORG" not in states(result)
        assert result["scope"] == "organisation"
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "care_gap.sglt2_reviewed")) is not None
        for user in (cohort.researcher, cohort.caio):
            with pytest.raises(PermissionError):
                care_gaps_for_user(db, db.get(User, user.id), 10)


def test_rest_route_roles_and_limits(cohort):
    def get(user, **params):
        return cohort.client.get("/api/care-gaps/sglt2", params=params, headers={"Authorization": f"Bearer {create_token(user)}"})
    assert "OTHER-ORG" in states(get(cohort.admin).json())
    assert "OTHER-ORG" not in states(get(cohort.clinician).json())
    assert get(cohort.researcher).status_code == 403
    assert get(cohort.caio).status_code == 403
    assert get(cohort.admin, limit=51).status_code == 422
    assert cohort.client.get("/api/care-gaps/sglt2").status_code == 401


def test_goola_answers_with_review_list(cohort):
    with cohort.factory() as db:
        reply = chat(db, db.get(User, cohort.clinician.id), "Goola, list T2DM patients with ASCVD or eGFR <60 not yet on SGLT2-i")
        assert reply["tool_events"][0]["type"] == "sglt2_care_gap"
        assert "ASCVD-GAP" in reply["message"] and "OTHER-ORG" not in reply["message"]
        assert "not a prescription" in reply["message"]
        denied = chat(db, db.get(User, cohort.researcher.id), "list care gaps for SGLT2")
        assert denied["tool_events"] == []


def test_mcp_tool_uses_clinical_boundary(cohort, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", cohort.factory)
    with cohort.factory() as db:
        def key_for(user, scopes):
            key = AgentAccessKey(token_hash=f"hash-{user.id}-{len(scopes)}", name="t", purpose="t", user_id=user.id, approved_by=cohort.admin.id,
                                 scopes=scopes, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
            db.add(key); db.commit()
            return key.id
        clinician_key = key_for(cohort.clinician, ["status:read", "clinical:read:organisation"])
        caio_key = key_for(cohort.caio, ["clinical:read:system"])
        status_only = key_for(cohort.clinician, ["status:read"])
    token = mcp_server._key_id.set(clinician_key)
    try:
        assert "OTHER-ORG" not in states(mcp_server.sglt2_care_gap_review(50))
    finally:
        mcp_server._key_id.reset(token)
    token = mcp_server._key_id.set(caio_key)
    try:
        assert "OTHER-ORG" in states(mcp_server.sglt2_care_gap_review(50))
    finally:
        mcp_server._key_id.reset(token)
    token = mcp_server._key_id.set(status_only)
    try:
        with pytest.raises(ValueError):
            mcp_server.sglt2_care_gap_review(10)
    finally:
        mcp_server._key_id.reset(token)


def test_seed_generators_cover_review_fixtures():
    assert all(synthetic_egfr(i) is not None for i in range(1, 501)) and synthetic_egfr(49) == 17.0  # every profile has an eGFR
    assert all(20 <= synthetic_egfr(i) < 60 for i in range(6, 600, 6) if i % 100 not in {9, 49})
    assert synthetic_ascvd_code(5) and synthetic_ascvd_code(7) is None
    stopped = synthetic_medications(35, "p", 70.0, True)
    assert any(item.status == "stopped" and item.atc_code == "A10BK01" for item in stopped)
    unmapped = synthetic_medications(65, "p", 70.0, True)
    assert any(item.terminology_status == "needs_terminology_review" for item in unmapped)
    assert not any(item.atc_code == "A10BA02" for item in synthetic_medications(49, "p", 17.0, False))  # no metformin below eGFR 30
