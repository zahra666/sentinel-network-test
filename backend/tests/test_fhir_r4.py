import copy

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import FhirImportPayload, fhir_everything, fhir_import, fhir_patient
from app.models import Condition, DiagnosticReport, Observation, Organization, Patient, User
from app.security import require_roles
from app.terminology import LOINC_SYSTEM, map_measurement


def patient_entry(patient_id: str, full_url: str = "urn:uuid:patient-1") -> dict:
    return {"fullUrl": full_url, "resource": {
        "resourceType": "Patient", "id": patient_id,
        "identifier": [{"system": "urn:sentinel:synthetic-patient-reference", "value": "SYN-FHIR-001"}],
    }}


def observation_entry(version: int = 1, value: float = 7.2, source_id: str = "FHIR-OBS-1", code: str = "4548-4", ucum: str = "%", local_code: str = "HBA1C") -> dict:
    return {"fullUrl": f"urn:uuid:obs-{source_id}-{version}", "resource": {
        "resourceType": "Observation", "id": f"obs-{source_id}-{version}", "meta": {"versionId": str(version)},
        "identifier": [{"system": "urn:sentinel:source-id", "value": source_id}], "status": "corrected" if version > 1 else "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": "Untrusted source display"}]},
        "subject": {"reference": "urn:uuid:patient-1"}, "effectiveDateTime": "2026-11-04T09:30:00+08:00",
        "issued": "2026-11-04T10:00:00+08:00",
        "valueQuantity": {"value": value, "unit": ucum, "system": "http://unitsofmeasure.org", "code": ucum},
        "extension": [{"url": "https://sentinel.demo/fhir/StructureDefinition/original-local-test-code", "valueString": local_code}],
    }}


def condition_entry() -> dict:
    return {"resource": {
        "resourceType": "Condition", "id": "condition-demo", "meta": {"versionId": "1"},
        "identifier": [{"system": "urn:sentinel:source-id", "value": "FHIR-COND-1"}],
        "subject": {"reference": "urn:uuid:patient-1"},
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
        "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]},
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "44054006", "display": "Type 2 diabetes mellitus"}]},
        "recordedDate": "2026-11-04T10:00:00+08:00",
    }}


def report_entry() -> dict:
    return {"resource": {
        "resourceType": "DiagnosticReport", "id": "report-demo", "meta": {"versionId": "1"},
        "identifier": [{"system": "urn:sentinel:source-id", "value": "FHIR-REPORT-1"}], "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "55399-0", "display": "Diabetes tracking panel"}]},
        "subject": {"reference": "urn:uuid:patient-1"}, "effectiveDateTime": "2026-11-04T10:00:00+08:00",
        "issued": "2026-11-04T10:30:00+08:00", "result": [{"reference": "urn:uuid:obs-FHIR-OBS-1-1"}],
    }}


def bundle(patient_id: str) -> dict:
    return {"resourceType": "Bundle", "type": "collection", "entry": [patient_entry(patient_id), observation_entry(), condition_entry(), report_entry()]}


@pytest.fixture
def fhir_access():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        org = Organization(code="FHIR-A", name="FHIR A", district="FHIR District")
        other = Organization(code="FHIR-B", name="FHIR B", district="Other District")
        db.add_all([org, other]); db.flush()
        admin = User(email="fhir-admin@test.demo", password_hash="unused", role="administrator")
        clinician = User(email="fhir-clinician@test.demo", password_hash="unused", role="clinician", organization_id=org.id)
        researcher = User(email="fhir-researcher@test.demo", password_hash="unused", role="researcher")
        db.add_all([admin, clinician, researcher]); db.flush()
        patient = Patient(reference="SYN-FHIR-001", organization_id=org.id, care_setting="Outpatient Clinic", age_group="45-64")
        outside = Patient(reference="SYN-FHIR-002", organization_id=other.id, care_setting="Medical Ward", age_group="65+")
        db.add_all([patient, outside]); db.commit()

    yield type("Access", (), {"factory": factory, "patient": patient, "outside": outside,
                                "admin": admin, "clinician": clinician, "researcher": researcher})
    engine.dispose()


def test_fhir_round_trip_and_duplicate_import(fhir_access):
    with fhir_access.factory() as db:
        imported = fhir_import(FhirImportPayload(bundle=bundle(fhir_access.patient.id)), fhir_access.admin, db)
        assert {item["status"] for item in imported["results"]} == {"matched", "imported"}
        assert fhir_patient(fhir_access.patient.id, fhir_access.admin, db)["resourceType"] == "Patient"
        everything = fhir_everything(fhir_access.patient.id, fhir_access.admin, db)
        duplicate = fhir_import(FhirImportPayload(bundle=bundle(fhir_access.patient.id)), fhir_access.admin, db)
    resources = [entry["resource"] for entry in everything["entry"]]
    assert {resource["resourceType"] for resource in resources} == {"Patient", "Observation", "Condition", "DiagnosticReport"}
    observation = next(resource for resource in resources if resource["resourceType"] == "Observation")
    assert observation["code"]["coding"][0]["code"] == "4548-4"
    assert observation["valueQuantity"]["code"] == "%"
    report = next(resource for resource in resources if resource["resourceType"] == "DiagnosticReport")
    assert report["result"][0]["reference"].startswith("Observation/")
    assert [item["status"] for item in duplicate["results"]].count("duplicate") == 3
    with fhir_access.factory() as db:
        assert db.scalar(select(Observation).where(Observation.source_id == "FHIR-OBS-1")) is not None
        assert len(list(db.scalars(select(Condition)))) == 1
        assert len(list(db.scalars(select(DiagnosticReport)))) == 1


def test_fhir_rejects_invalid_resource_and_incompatible_known_unit(fhir_access):
    invalid = bundle(fhir_access.patient.id)
    invalid["entry"][1]["resource"].pop("effectiveDateTime")
    with fhir_access.factory() as db:
        with pytest.raises(HTTPException) as response:
            fhir_import(FhirImportPayload(bundle=invalid), fhir_access.admin, db)
    assert response.value.status_code == 422
    incompatible = {"resourceType": "Bundle", "type": "collection", "entry": [patient_entry(fhir_access.patient.id), observation_entry(ucum="mmol/L")]}
    with fhir_access.factory() as db:
        with pytest.raises(HTTPException) as response:
            fhir_import(FhirImportPayload(bundle=incompatible), fhir_access.admin, db)
    assert response.value.status_code == 422
    assert "incompatible" in response.value.detail


def test_fhir_flags_ambiguous_mapping_and_versions_corrections(fhir_access):
    ambiguous = {"resourceType": "Bundle", "type": "collection", "entry": [patient_entry(fhir_access.patient.id), observation_entry(source_id="UNMAPPED", code="99999-9", local_code="UNKNOWN")]}
    with fhir_access.factory() as db:
        response = fhir_import(FhirImportPayload(bundle=ambiguous), fhir_access.admin, db)
    assert response["reprocess_required"] is True
    with fhir_access.factory() as db:
        record = db.scalar(select(Observation).where(Observation.source_id == "UNMAPPED"))
        assert record.terminology_status == "needs_terminology_review"
        assert record.clinical_key is None

    first = {"resourceType": "Bundle", "type": "collection", "entry": [patient_entry(fhir_access.patient.id), observation_entry(source_id="CORRECTED", value=7.1)]}
    with fhir_access.factory() as db:
        assert fhir_import(FhirImportPayload(bundle=first), fhir_access.admin, db)["reprocess_required"] is True
    corrected = copy.deepcopy(first)
    corrected["entry"][1] = observation_entry(version=2, source_id="CORRECTED", value=7.4)
    with fhir_access.factory() as db:
        response = fhir_import(FhirImportPayload(bundle=corrected), fhir_access.admin, db)
    assert response["reprocess_required"] is True
    with fhir_access.factory() as db:
        history = list(db.scalars(select(Observation).where(Observation.source_id == "CORRECTED").order_by(Observation.version)).all())
        assert len(history) == 2
        assert history[1].corrects_observation_id == history[0].id
        assert history[1].provenance["original_quantity"]["value"] == 7.4


def test_fhir_patient_reference_and_role_boundaries(fhir_access):
    wrong_patient = bundle(fhir_access.outside.id)
    with fhir_access.factory() as db:
        with pytest.raises(HTTPException) as response:
            fhir_import(FhirImportPayload(bundle=wrong_patient), fhir_access.clinician, db)
    assert response.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        require_roles("administrator", "clinician")(fhir_access.researcher)
    assert denied.value.status_code == 403


def test_only_explicit_unit_conversions_are_available():
    hba1c = map_measurement(system=LOINC_SYSTEM, code="59261-8", display=None, version=None,
                            original_code="HBA1C_IFCC", value=53.0, ucum_unit="mmol/mol")
    assert hba1c["normalised_ucum_unit"] == "%"
    assert hba1c["normalised_value"] == pytest.approx(7.0004)
    glucose = map_measurement(system=LOINC_SYSTEM, code="2345-7", display=None, version=None,
                              original_code="FPG", value=180.156, ucum_unit="mg/dL")
    assert glucose["clinical_key"] == "fpg"
    assert glucose["normalised_value"] == pytest.approx(10.0)
