"""Synthetic renal profile: terminology, seed consistency, chart payload and access boundaries."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_sentinel.db")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ["SEED_PATIENT_COUNT"] = "100"

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.guideline_tree import assessment
from app.main import app
from app.models import Observation, Organization, Patient, User
from app.renal_profile import ANALYTES, ckd_epi_2021, creatinine_for_egfr, flag, kdigo_g_category, renal_profile, synthetic_renal_panels
from app.security import create_token
from app.seed import synthetic_egfr
from app.terminology import LOINC_SYSTEM, LOINC_VERSION, TerminologyError, compatible_for_rule, map_measurement

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
FIELDS = ("original_code", "coding_system", "standard_code", "coding_display", "terminology_version", "clinical_key", "terminology_status", "normalised_value", "normalised_ucum_unit")


def lab(patient, loinc, local, value, unit, *, suffix, days_ago=5, version=1):
    mapping = map_measurement(system=LOINC_SYSTEM, code=loinc, display=None, version=LOINC_VERSION, original_code=local, value=value, ucum_unit=unit)
    return Observation(patient_id=patient.id, code=mapping["clinical_key"] or local.lower(), value=value, unit=unit, ucum_unit=unit,
                       observed_at=NOW - timedelta(days=days_ago), ingested_at=NOW - timedelta(days=days_ago) + timedelta(hours=version),
                       source_id=f"R-{patient.reference}-{suffix}", version=version, **{key: mapping[key] for key in FIELDS})


@pytest.mark.parametrize("loinc,local,unit,key", [
    ("2951-2", "NA", "mmol/L", "sodium"), ("2823-3", "K", "mmol/L", "potassium"), ("2075-0", "CL", "mmol/L", "chloride"),
    ("14682-9", "CREAT_UMOL", "umol/L", "creatinine_molar"), ("22664-7", "UREA", "mmol/L", "urea"), ("14933-6", "URIC_ACID", "umol/L", "urate"),
])
def test_renal_terms_map_verified_and_reject_wrong_units(loinc, local, unit, key):
    mapping = map_measurement(system=LOINC_SYSTEM, code=loinc, display=None, version=LOINC_VERSION, original_code=local, value=1.0, ucum_unit=unit)
    assert (mapping["clinical_key"], mapping["terminology_status"], mapping["normalised_ucum_unit"]) == (key, "verified", unit)
    assert compatible_for_rule(SimpleNamespace(**mapping, result_status="final"), key)
    with pytest.raises(TerminologyError):
        map_measurement(system=LOINC_SYSTEM, code=loinc, display=None, version=LOINC_VERSION, original_code=local, value=1.0, ucum_unit="mg/dL")


def test_conflicting_local_context_is_ambiguous():
    mapping = map_measurement(system=LOINC_SYSTEM, code="2951-2", display=None, version=LOINC_VERSION, original_code="K", value=140, ucum_unit="mmol/L")
    assert mapping["terminology_status"] == "ambiguous_local_mapping" and mapping["clinical_key"] is None


def test_ckd_epi_2021_inverse_round_trips():
    for egfr in (17, 32, 58, 71, 95):
        for female in (True, False):
            assert abs(ckd_epi_2021(creatinine_for_egfr(egfr, 55, female), 55, female) - egfr) < 0.01
    # Published-equation spot check: 55-year-old man, creatinine 1.0 mg/dL -> 142 x (1.0/0.9)^-1.2 x 0.9938^55 = 88.9.
    assert round(ckd_epi_2021(88.4, 55, False)) == 89


def test_seed_panels_keep_latest_egfr_and_are_internally_consistent():
    for index in range(1, 101):
        final = synthetic_egfr(index)
        panels = synthetic_renal_panels(index, "45-64", final)
        assert len(panels) == 3
        assert all(code != "egfr" for code, *_ in panels[-1])  # latest eGFR is the seed's existing record
        for panel in panels[:-1]:
            codes = [code for code, *_ in panel]
            assert codes == (["sodium", "potassium", "chloride", "urea", "uric_acid"] if final is None else ["sodium", "potassium", "chloride", "creatinine", "urea", "uric_acid", "egfr"])
            if final is not None:
                # Earlier visits never look better than the latest beyond a small measurement wobble.
                assert dict((c, v) for c, v, *_ in panel)["egfr"] >= final - 1.5
        assert all(isinstance(value, float) for panel in panels for _, value, *_ in panel)  # integrity hashes need stable floats
    assert synthetic_renal_panels(9, "18-44", None)[0][0][0] == "sodium"


def test_flags_and_categories_are_display_only():
    assert flag("creatinine", 110.7) == "H" and flag("potassium", 3.4) == "L" and flag("sodium", 140) is None
    assert flag("egfr", 70.5) is None and flag("egfr", 47) == "L"
    assert [kdigo_g_category(v) for v in (95, 70, 50, 35, 20, 10)] == ["G1", "G2", "G3a", "G3b", "G4", "G5"]


@pytest.fixture
def cohort():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        a, b = Organization(code="A", name="Synthetic A", district="D1"), Organization(code="B", name="Synthetic B", district="D2")
        db.add_all([a, b]); db.flush()
        admin = User(email="admin@t.demo", password_hash="x", role="administrator")
        clinician = User(email="clin@t.demo", password_hash="x", role="clinician", organization_id=a.id)
        researcher = User(email="res@t.demo", password_hash="x", role="researcher")
        mine = Patient(reference="RENAL-A", organization_id=a.id, care_setting="Outpatient Clinic", age_group="45-64")
        other = Patient(reference="RENAL-B", organization_id=b.id, care_setting="Outpatient Clinic", age_group="45-64")
        db.add_all([admin, clinician, researcher, mine, other]); db.flush()
        rows = []
        for days_ago, (na, k, cl, cr, urea, ua, egfr) in ((190, (139, 4.1, 104, 104.0, 4.8, 330.0, 74.0)), (8, (140, 4.0, 108, 110.7, 4.5, 342.66, 70.53))):
            values = dict(zip(("sodium", "potassium", "chloride", "creatinine", "urea", "uric_acid", "egfr"), (na, k, cl, cr, urea, ua, egfr)))
            for code, loinc, local, _label, unit, _d in ANALYTES:
                rows.append(lab(mine, loinc, local, float(values[code]), unit, suffix=f"{code}-{days_ago}", days_ago=days_ago))
        rows.append(lab(mine, "2823-3", "K", 6.9, "mmol/L", suffix="k-fix", days_ago=3))
        rows.append(lab(mine, "2823-3", "K", 4.2, "mmol/L", suffix="k-fix", days_ago=3, version=2))  # corrected haemolysed potassium
        rows.append(lab(other, "2951-2", "NA", 141.0, "mmol/L", suffix="na"))
        db.add_all(rows); db.commit()

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    client = TestClient(app)
    yield SimpleNamespace(factory=factory, client=client, admin=admin, clinician=clinician, researcher=researcher, mine=mine, other=other)
    app.dependency_overrides.pop(get_db, None)
    client.close()
    engine.dispose()


def auth(user):
    return {"Authorization": f"Bearer {create_token(user)}"}


def test_renal_profile_endpoint_series_flags_and_corrections(cohort):
    response = cohort.client.get(f"/api/patients/{cohort.mine.id}/renal-profile", headers=auth(cohort.clinician))
    assert response.status_code == 200
    body = response.json()
    by_code = {item["code"]: item for item in body["analytes"]}
    assert list(by_code) == ["sodium", "potassium", "chloride", "creatinine", "urea", "uric_acid", "egfr"]
    assert [p["value"] for p in by_code["creatinine"]["series"]] == [104.0, 110.7]
    assert by_code["creatinine"]["latest"]["flag"] == "H" and by_code["chloride"]["latest"]["flag"] == "H"
    assert by_code["egfr"]["category"] == "G2" and by_code["egfr"]["latest"]["flag"] is None
    potassium = by_code["potassium"]["series"]
    assert [p["value"] for p in potassium] == [4.1, 4.0, 4.2]  # corrected v2 supersedes v1, not double-plotted
    assert potassium[-1]["version"] == 2 and body["synthetic_only"] is True


def test_renal_profile_access_boundaries(cohort):
    assert cohort.client.get(f"/api/patients/{cohort.other.id}/renal-profile", headers=auth(cohort.clinician)).status_code == 404
    assert cohort.client.get(f"/api/patients/{cohort.other.id}/renal-profile", headers=auth(cohort.admin)).status_code == 200
    assert cohort.client.get(f"/api/patients/{cohort.mine.id}/renal-profile", headers=auth(cohort.researcher)).status_code == 403
    assert cohort.client.get(f"/api/patients/{cohort.mine.id}/renal-profile").status_code in {401, 403}


def test_renal_observations_do_not_change_cpg_review_inputs(cohort):
    with cohort.factory() as db:
        patient = db.get(Patient, cohort.mine.id)
        result = assessment(patient, list(patient.observations))
    assert result["status"] != "data_quality_review"  # verified renal terms are not treated as unmapped data
    assert result["nodes"][0]["state"] == "valid"
