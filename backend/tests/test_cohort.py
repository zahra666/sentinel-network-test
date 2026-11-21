"""Cohort profile: NDR-calibrated hypertension, Goola count answer and access boundaries."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_sentinel.db")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ["SEED_PATIENT_COUNT"] = "100"

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import _is_hypertension_question, chat
from app.cohort import NDR_2023_HYPERTENSION_PCT, cohort_profile
from app.db import Base, get_db
from app.main import app
from app.models import Condition, Organization, Patient, User
from app.security import create_token
from app.seed import cohort_plan, hypertension_plan
from app.terminology import ASCVD_SNOMED_CODES, HYPERTENSION_SNOMED_CODE, SNOMED_SYSTEM, verified_condition


def test_hypertension_share_matches_ndr_2023_and_rises_with_age():
    chosen = hypertension_plan(500)
    assert len(chosen) == round(500 * NDR_2023_HYPERTENSION_PCT / 100) == 406
    attributes, _, _ = cohort_plan(500)
    share = {group: sum(r["index"] in chosen for r in attributes if r["age_group"] == group) / sum(r["age_group"] == group for r in attributes)
             for group in ("18-44", "45-64", "65+")}
    assert share["18-44"] < share["45-64"] < share["65+"] < 1.0


def test_hypertension_code_is_verified_and_not_ascvd():
    entry = verified_condition(SNOMED_SYSTEM, HYPERTENSION_SNOMED_CODE)
    assert entry["display"] == "Hypertensive disorder, systemic arterial (disorder)"
    assert HYPERTENSION_SNOMED_CODE not in ASCVD_SNOMED_CODES  # does not change the SGLT2 care-gap criterion


@pytest.mark.parametrize("text,expected", [
    ("how many patients have high blood pressure?", True), ("count hypertensive records", True), ("多少人有高血压", True),
    ("bp count please", True), ("list patients not on sglt2", False), ("subpoena", False),
])
def test_goola_recognises_hypertension_questions(text, expected):
    assert _is_hypertension_question(text.lower()) is expected


@pytest.fixture
def cohort():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        a, b = Organization(code="A", name="A", district="D1"), Organization(code="B", name="B", district="D2")
        db.add_all([a, b]); db.flush()
        admin = User(email="admin@t.demo", password_hash="x", role="administrator")
        clinician = User(email="clin@t.demo", password_hash="x", role="clinician", organization_id=a.id)
        researcher = User(email="res@t.demo", password_hash="x", role="researcher")
        pats = [Patient(reference=f"P{i}", organization_id=(a if i < 3 else b).id, care_setting="Outpatient Clinic", age_group="45-64",
                        sex="female" if i % 2 else "male") for i in range(4)]
        db.add_all([admin, clinician, researcher, *pats]); db.flush()

        def htn(patient, status="active", terminology="verified", suffix="1"):
            return Condition(patient_id=patient.id, source_id=f"H-{patient.reference}-{suffix}", clinical_status=status, verification_status="confirmed",
                             source_coding_system=SNOMED_SYSTEM, source_code=HYPERTENSION_SNOMED_CODE, snomed_code=HYPERTENSION_SNOMED_CODE,
                             snomed_display="Hypertensive disorder, systemic arterial (disorder)", terminology_status=terminology)
        # P0 male + P1 female hypertensive; P2 only a resolved entry (not counted); P3 (other hospital) hypertensive.
        db.add_all([htn(pats[0]), htn(pats[1]), htn(pats[2], status="resolved"), htn(pats[3])])
        db.commit()

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    client = TestClient(app)
    yield SimpleNamespace(client=client, factory=factory, admin=admin, clinician=clinician, researcher=researcher)
    app.dependency_overrides.pop(get_db, None)
    client.close()
    engine.dispose()


def auth(user):
    return {"Authorization": f"Bearer {create_token(user)}"}


def test_cohort_summary_counts_and_scope(cohort):
    mine = cohort.client.get("/api/cohort/summary", headers=auth(cohort.clinician)).json()
    assert (mine["patients"], mine["hypertension"]["count"], mine["hypertension"]["by_sex"]) == (3, 2, {"female": 1, "male": 1})
    everyone = cohort.client.get("/api/cohort/summary", headers=auth(cohort.admin)).json()
    assert everyone["hypertension"]["count"] == 3 and everyone["egfr"]["not_tested"] == 4
    assert cohort.client.get("/api/cohort/summary", headers=auth(cohort.researcher)).status_code == 403


def test_goola_answers_with_scoped_count_only(cohort):
    with cohort.factory() as db:
        answer = chat(db, db.get(User, cohort.clinician.id), "How many patients have high blood pressure?")
        assert answer["message"].startswith("2 of 3 synthetic T2DM records in your hospital")
        assert "P0" not in answer["message"] and answer["tool_events"][0]["count"] == 2
        denied = chat(db, db.get(User, cohort.researcher.id), "how many hypertension")
        assert denied["message"].startswith("Only administrators and clinicians")
