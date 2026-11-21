"""NDR-calibrated glucose-lowering treatment modality: calibration, consistency and access."""
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

from app.db import Base, get_db
from app.main import app
from app.models import MedicationStatement, Organization, Patient, User
from app.security import create_token
from app.seed import abnormal_glycaemia, cohort_plan, synthetic_ascvd_code, synthetic_medications, synthetic_on_sglt2
from app.terminology import ATC_MEDICATIONS, ATC_SYSTEM
from app.treatment import NDR_2023, Profile, cohort_summary, largest_remainder, modality_of, plan_treatment, MODALITY_SHARES


def profiles(n):
    attributes, _, egfr_plan = cohort_plan(n)
    out = []
    for row in attributes:
        i = row["index"]
        egfr, ascvd = egfr_plan[i], synthetic_ascvd_code(i) is not None
        out.append(Profile(index=i, egfr=egfr, has_ascvd=ascvd, on_sglt2=synthetic_on_sglt2(i, egfr, ascvd),
                           abnormal_glycaemia=abnormal_glycaemia(i), age_group=row["age_group"],
                           care_setting=row["care_setting"], pregnancy_related=row["pregnancy_related"]))
    return out


def cohort_modalities(n):
    plan = plan_treatment(profiles(n))
    result = []
    for p in profiles(n):
        result.append(modality_of(synthetic_medications(p.index, f"P{p.index}", p.egfr, p.has_ascvd, plan[p.index])))
    return plan, result


def test_ndr_reference_values_are_internally_consistent():
    assert round(NDR_2023["insulin_any"] - NDR_2023["oad_plus_insulin"], 2) == NDR_2023["insulin_only_derived"]
    assert abs(sum(MODALITY_SHARES.values()) - 100) < 0.05
    assert sum(largest_remainder(500, MODALITY_SHARES).values()) == 500


def test_500_cohort_matches_ndr_2023_within_one_point():
    _, modalities = cohort_modalities(500)
    summary = cohort_summary(modalities)
    rows = {row["key"]: row for row in summary["modalities"]}
    for key in ("insulin_plus_oral", "insulin_only", "diet_only"):
        assert abs(rows[key]["cohort_pct"] - rows[key]["ndr_2023_pct"]) <= 0.5
    # Oral-only is lower only by the deliberate unmapped-medication fixture (1 per 100).
    assert abs(rows["oral_only"]["cohort_pct"] + 100 * summary["needs_review"] / 500 - rows["oral_only"]["ndr_2023_pct"]) <= 0.5
    for row in summary["detail"]:
        assert abs(row["cohort_pct"] - row["ndr_2023_pct"]) <= 1.5, row


def test_regimens_are_clinically_coherent():
    plan, modalities = cohort_modalities(500)
    for p in profiles(500):
        meds = synthetic_medications(p.index, "x", p.egfr, p.has_ascvd, plan[p.index])
        atc = {m.atc_code for m in meds if m.status == "active"}
        if p.egfr is not None and p.egfr < 30:
            assert "A10BA02" not in atc  # no metformin below eGFR 30
        if plan[p.index].modality in {"diet_only", "insulin_only"}:
            assert not atc & {"A10BA02", "A10BB09", "A10BK01", "A10BK03"}
    insulin_routes = {ATC_MEDICATIONS[c]["route"] for c in ("A10AC01", "A10AD01")}
    assert insulin_routes == {"injection"}  # insulin is injected; "oral" means oral glucose-lowering drugs


def test_sglt2_status_and_fixtures_unchanged_by_calibration():
    plan = plan_treatment(profiles(200))
    for p in profiles(200):
        legacy = {m.atc_code for m in synthetic_medications(p.index, "x", p.egfr, p.has_ascvd) if m.atc_code and m.atc_code.startswith("A10BK") and m.status == "active"}
        calibrated = {m.atc_code for m in synthetic_medications(p.index, "x", p.egfr, p.has_ascvd, plan[p.index]) if m.atc_code and m.atc_code.startswith("A10BK") and m.status == "active"}
        assert legacy == calibrated
    assert plan[35].modality == plan[65].modality == "oral_only"
    assert modality_of(synthetic_medications(65, "x", 70.0, True, plan[65]))["modality"] == "medication_list_needs_review"


def test_insulin_is_weighted_to_poorer_control_and_kidney_function():
    plan = plan_treatment(profiles(500))
    insulin = [p for p in profiles(500) if plan[p.index].insulin_atc]
    others = [p for p in profiles(500) if not plan[p.index].insulin_atc]
    share = lambda group, f: sum(map(f, group)) / len(group)  # noqa: E731
    assert share(insulin, lambda p: p.abnormal_glycaemia) > share(others, lambda p: p.abnormal_glycaemia)
    assert share(insulin, lambda p: p.egfr is not None and p.egfr < 45) > share(others, lambda p: p.egfr is not None and p.egfr < 45)


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
        pats = {name: Patient(reference=name, organization_id=(b if name == "B-ORAL" else a).id, care_setting="Outpatient Clinic", age_group="45-64")
                for name in ("A-INS-ORAL", "A-DIET", "B-ORAL")}
        db.add_all([admin, clinician, researcher, *pats.values()]); db.flush()

        def med(patient, atc, suffix):
            return MedicationStatement(patient_id=patient.id, source_id=f"M-{patient.reference}-{suffix}", status="active", source_coding_system=ATC_SYSTEM,
                                       source_code=atc, atc_code=atc, atc_display=ATC_MEDICATIONS[atc]["display"], terminology_status="verified")
        db.add_all([med(pats["A-INS-ORAL"], "A10BA02", "1"), med(pats["A-INS-ORAL"], "A10AC01", "2"), med(pats["B-ORAL"], "A10BB09", "1")])
        db.commit()

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    client = TestClient(app)
    yield SimpleNamespace(client=client, admin=admin, clinician=clinician, researcher=researcher, p=pats)
    app.dependency_overrides.pop(get_db, None)
    client.close()
    engine.dispose()


def auth(user):
    return {"Authorization": f"Bearer {create_token(user)}"}


def test_patient_list_shows_treatment_column_with_org_boundary(cohort):
    rows = {row["reference"]: row["treatment"]["modality"] for row in cohort.client.get("/api/patients", headers=auth(cohort.clinician)).json()}
    assert rows == {"A-INS-ORAL": "insulin_plus_oral", "A-DIET": "diet_only"}
    detail = cohort.client.get(f"/api/patients/{cohort.p['A-INS-ORAL'].id}", headers=auth(cohort.clinician)).json()["treatment"]
    assert {m["route"] for m in detail["medications"]} == {"oral", "injection"}


def test_treatment_summary_scope_and_roles(cohort):
    mine = cohort.client.get("/api/treatment/summary", headers=auth(cohort.clinician)).json()
    assert mine["patients"] == 2 and mine["scope"] == "organisation"
    everyone = cohort.client.get("/api/treatment/summary", headers=auth(cohort.admin)).json()
    assert everyone["patients"] == 3 and "National Diabetes Registry Report 2023" in everyone["reference"]["source"]
    assert cohort.client.get("/api/treatment/summary", headers=auth(cohort.researcher)).status_code == 403
