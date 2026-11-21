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
from app.cohort import EGFR_STAGES, NDR_2023_SEX, egfr_category
from app.fhir_r4 import patient_resource
from app.renal_profile import ANALYTES, ckd_epi_2021, creatinine_for_egfr, flag, kdigo_g_category, reference_for, renal_profile, synthetic_renal_panels
from app.security import create_token
from app.seed import cohort_plan
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
    attributes, sex_plan, egfr_plan = cohort_plan(100)
    for row in attributes:
        index, final, sex = row["index"], egfr_plan[row["index"]], sex_plan[row["index"]]
        panels = synthetic_renal_panels(index, row["age_group"], final, sex)
        assert len(panels) == 3
        if final is None:  # creatinine not tested: the other five analytes are still reported
            assert all([code for code, *_ in panel] == ["sodium", "potassium", "chloride", "urea", "uric_acid"] for panel in panels)
            continue
        assert [code for code, *_ in panels[-1]] == ["sodium", "potassium", "chloride", "creatinine", "urea", "uric_acid"]  # latest eGFR is the seed's existing record
        for panel in panels[:-1]:
            assert [code for code, *_ in panel] == ["sodium", "potassium", "chloride", "creatinine", "urea", "uric_acid", "egfr"]
            assert dict((c, v) for c, v, *_ in panel)["egfr"] >= final - 1.5
        # Creatinine is consistent with the patient's own sex in CKD-EPI 2021.
        creatinine = dict((c, v) for c, v, *_ in panels[-1])["creatinine"]
        assert abs(ckd_epi_2021(creatinine, {"18-44": 38, "45-64": 55, "65+": 71}[row["age_group"]], sex == "female") - final) < 0.2
        assert all(isinstance(value, float) for panel in panels for _, value, *_ in panel)  # integrity hashes need stable floats


def test_cohort_sex_and_egfr_follow_malaysian_data():
    attributes, sex_plan, egfr_plan = cohort_plan(500)
    female = sum(value == "female" for value in sex_plan.values())
    assert female == round(500 * NDR_2023_SEX["female"] / 100)  # 285 of 500 (NDR 2023: 57.08 %)
    assert all(sex_plan[row["index"]] == "female" for row in attributes if row["care_setting"] == "O&G Ward")
    tested = [value for value in egfr_plan.values() if value is not None]
    assert len(tested) == 455  # 90.99 % creatinine tested (NDR 2023)
    for name, _low, _high, share in EGFR_STAGES:
        assert abs(100 * sum(egfr_category(v) == name for v in tested) / len(tested) - share) <= 0.5, name
    by_age = {group: [egfr_plan[r["index"]] for r in attributes if r["age_group"] == group and egfr_plan[r["index"]] is not None] for group in ("18-44", "65+")}
    assert sum(by_age["65+"]) / len(by_age["65+"]) < sum(by_age["18-44"]) / len(by_age["18-44"]) - 20  # kidney function falls with age


def test_sex_specific_reference_intervals_and_fhir_gender():
    assert reference_for("creatinine", "female")["high"] == 90 and reference_for("creatinine", "male")["high"] == 104
    assert reference_for("uric_acid", "female")["high"] == 360 and reference_for("uric_acid", "male")["high"] == 430
    assert flag("creatinine", 95, "female") == "H" and flag("creatinine", 95, "male") is None
    assert reference_for("creatinine", None) == {"low": 49, "high": 104, "note": "sex not recorded; combined female/male span"}
    assert patient_resource(SimpleNamespace(id="p", reference="SYN", care_setting="Outpatient Clinic", sex="female"))["gender"] == "female"
    assert "gender" not in patient_resource(SimpleNamespace(id="p", reference="SYN", care_setting="Outpatient Clinic", sex=None))


def test_flags_and_categories_are_display_only():
    assert flag("creatinine", 110.7, "male") == "H" and flag("potassium", 3.4) == "L" and flag("sodium", 140) is None
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


def test_every_seeded_patient_has_a_renal_profile():
    """All 500 profiles have 3 renal panels; 455 have all 7 analytes, 45 (creatinine not tested) have 5."""
    import tempfile
    from sqlalchemy import create_engine as _ce, select as _select
    from sqlalchemy.orm import sessionmaker as _sm
    from app import seed as seed_module
    from app.config import get_settings

    engine = _ce(f"sqlite:///{tempfile.mkdtemp()}/renal.db")
    Base.metadata.create_all(engine)
    factory = _sm(bind=engine, expire_on_commit=False)
    original = (seed_module.SessionLocal, seed_module.initialise_database, get_settings().seed_patient_count)
    seed_module.SessionLocal, seed_module.initialise_database = factory, (lambda: None)
    get_settings().seed_patient_count = 500
    try:
        seed_module.seed()
        with factory() as db:
            patients = list(db.scalars(_select(Patient)).all())
            assert len(patients) == 500
            complete = without_creatinine = 0
            for patient in patients:
                assert patient.sex in {"female", "male"}
                profile = renal_profile(patient.observations, patient.sex)
                assert len(profile["panel_dates"]) == 3, patient.reference
                present = {a["code"] for a in profile["analytes"] if a["series"]}
                for analyte in profile["analytes"]:
                    if analyte["code"] in present:
                        assert len(analyte["series"]) == 3 and all(p["value"] is not None for p in analyte["series"]), (patient.reference, analyte["code"])
                complete += len(present) == 7
                without_creatinine += present == {"sodium", "potassium", "chloride", "urea", "uric_acid"}
            assert (complete, without_creatinine) == (455, 45)
            assert sum(p.sex == "female" for p in patients) == 285
    finally:
        seed_module.SessionLocal, seed_module.initialise_database = original[0], original[1]
        get_settings().seed_patient_count = original[2]
        engine.dispose()
