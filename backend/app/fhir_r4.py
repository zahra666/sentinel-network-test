"""Bounded HL7 FHIR R4 JSON mapping for Sentinel's synthetic demo.

This module is intentionally narrow: it serializes/imports the documented
Patient, Observation, Condition and DiagnosticReport subset. It is not a full
FHIR server, profile validator, terminology server, or production interface.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Condition, DiagnosticReport, Observation, Patient, User
from .terminology import LOINC_SYSTEM, SNOMED_SYSTEM, UCUM_SYSTEM, TerminologyError, map_measurement, validate_ucum_code, verified_condition

FHIR_VERSION = "4.0.1"
PATIENT_IDENTIFIER_SYSTEM = "urn:sentinel:synthetic-patient-reference"
SOURCE_IDENTIFIER_SYSTEM = "urn:sentinel:source-id"
LOCAL_TEST_CODE_URL = "https://sentinel.demo/fhir/StructureDefinition/original-local-test-code"
TERMINOLOGY_STATUS_URL = "https://sentinel.demo/fhir/StructureDefinition/terminology-status"
NORMALISED_VALUE_URL = "https://sentinel.demo/fhir/StructureDefinition/normalised-value"

OBSERVATION_STATUSES = {"registered", "preliminary", "final", "amended", "corrected", "cancelled", "entered-in-error", "unknown"}
REPORT_STATUSES = {"registered", "partial", "preliminary", "final", "amended", "corrected", "appended", "cancelled", "entered-in-error", "unknown"}


class FhirValidationError(ValueError):
    pass


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FhirValidationError(f"{label} must be a JSON object")
    return value


def _parse_time(value: Any, label: str, required: bool = True) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise FhirValidationError(f"{label} must be an ISO-8601 dateTime with an offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FhirValidationError(f"{label} must be an ISO-8601 dateTime") from exc
    if parsed.tzinfo is None:
        raise FhirValidationError(f"{label} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _fhir_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def _version(resource: dict[str, Any]) -> int:
    raw = resource.get("meta", {}).get("versionId", "1") if isinstance(resource.get("meta", {}), dict) else "1"
    try:
        number = int(str(raw))
    except ValueError as exc:
        raise FhirValidationError("meta.versionId must be a positive integer in this subset") from exc
    if number < 1:
        raise FhirValidationError("meta.versionId must be a positive integer in this subset")
    return number


def _identifier(resource: dict[str, Any], fallback: str | None = None) -> str:
    identifiers = resource.get("identifier", [])
    if not isinstance(identifiers, list):
        raise FhirValidationError("identifier must be an array")
    for item in identifiers:
        if isinstance(item, dict) and item.get("system") == SOURCE_IDENTIFIER_SYSTEM and isinstance(item.get("value"), str) and item["value"].strip():
            return item["value"].strip()
    raw = fallback or resource.get("id")
    if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 96:
        raise FhirValidationError(f"{SOURCE_IDENTIFIER_SYSTEM} identifier or a bounded resource id is required")
    return raw.strip()


def _coding(concept: Any, label: str, required: bool = True) -> dict[str, str | None] | None:
    if concept is None and not required:
        return None
    if not isinstance(concept, dict) or not isinstance(concept.get("coding"), list) or not concept["coding"]:
        raise FhirValidationError(f"{label}.coding is required in this supported subset")
    for coding in concept["coding"]:
        if not isinstance(coding, dict):
            continue
        system, code = coding.get("system"), coding.get("code")
        if isinstance(system, str) and system and isinstance(code, str) and code:
            return {"system": system, "code": code, "display": coding.get("display") if isinstance(coding.get("display"), str) else None,
                    "version": coding.get("version") if isinstance(coding.get("version"), str) else None}
    raise FhirValidationError(f"{label}.coding needs a system URI and code")


def _extension_string(resource: dict[str, Any], url: str) -> str | None:
    extensions = resource.get("extension", [])
    if not isinstance(extensions, list):
        raise FhirValidationError("extension must be an array")
    for extension in extensions:
        if isinstance(extension, dict) and extension.get("url") == url and isinstance(extension.get("valueString"), str):
            return extension["valueString"].strip() or None
    return None


def _patient_from_reference(db: Session, reference: Any, full_urls: dict[str, str] | None = None) -> Patient:
    if not isinstance(reference, dict):
        raise FhirValidationError("subject must be a Patient reference")
    ref = reference.get("reference")
    if isinstance(ref, str) and full_urls and ref in full_urls:
        ref = f"Patient/{full_urls[ref]}"
    patient: Patient | None = None
    if isinstance(ref, str) and ref.startswith("Patient/"):
        patient = db.get(Patient, ref.removeprefix("Patient/"))
    identifier = reference.get("identifier")
    if patient is None and isinstance(identifier, dict) and identifier.get("system") == PATIENT_IDENTIFIER_SYSTEM and isinstance(identifier.get("value"), str):
        patient = db.scalar(select(Patient).where(Patient.reference == identifier["value"]))
    if not patient:
        raise FhirValidationError("Patient reference does not resolve to an existing synthetic profile")
    return patient


def _authorise_patient(patient: Patient, user: User) -> None:
    if user.role not in {"administrator", "clinician"}:
        raise PermissionError("FHIR patient-level exchange requires a clinician or administrator")
    if user.role == "clinician" and patient.organization_id != user.organization_id:
        raise PermissionError("Patient is outside the clinician organisation boundary")


def patient_resource(patient: Patient) -> dict[str, Any]:
    resource = {
        "resourceType": "Patient", "id": patient.id, "meta": {"versionId": "1"}, "active": True,
        "identifier": [{"system": PATIENT_IDENTIFIER_SYSTEM, "value": patient.reference}],
        "extension": [
            {"url": "https://sentinel.demo/fhir/StructureDefinition/care-setting", "valueString": patient.care_setting},
            {"url": "https://sentinel.demo/fhir/StructureDefinition/synthetic-only", "valueBoolean": True},
        ],
    }
    if getattr(patient, "sex", None) in {"female", "male"}:
        resource["gender"] = patient.sex  # FHIR R4 AdministrativeGender
    return resource


def observation_resource(item: Observation) -> dict[str, Any]:
    coding = {"system": item.coding_system or "urn:sentinel:local-code", "code": item.standard_code or item.code,
              "display": item.coding_display or item.code}
    if item.terminology_version:
        coding["version"] = item.terminology_version
    extensions: list[dict[str, Any]] = [
        {"url": LOCAL_TEST_CODE_URL, "valueString": item.original_code or item.code},
        {"url": TERMINOLOGY_STATUS_URL, "valueString": item.terminology_status},
    ]
    if item.normalised_value is not None and item.normalised_ucum_unit:
        extensions.append({"url": NORMALISED_VALUE_URL, "extension": [
            {"url": "value", "valueDecimal": item.normalised_value},
            {"url": "unit", "valueCode": item.normalised_ucum_unit},
        ]})
    return {
        "resourceType": "Observation", "id": item.fhir_id or item.id, "meta": {"versionId": str(item.version)},
        "identifier": [{"system": SOURCE_IDENTIFIER_SYSTEM, "value": item.source_id}],
        "status": item.result_status, "code": {"coding": [coding]},
        "subject": {"reference": f"Patient/{item.patient_id}"},
        "effectiveDateTime": _fhir_time(item.observed_at), "issued": _fhir_time(item.ingested_at),
        "valueQuantity": {"value": item.value, "unit": item.unit, "system": UCUM_SYSTEM, "code": item.ucum_unit or item.unit},
        "extension": extensions,
    }


def condition_resource(item: Condition) -> dict[str, Any]:
    coding = {"system": item.source_coding_system or SNOMED_SYSTEM, "code": item.source_code or item.snomed_code,
              "display": item.source_display or item.snomed_display}
    if item.source_terminology_version:
        coding["version"] = item.source_terminology_version
    resource: dict[str, Any] = {
        "resourceType": "Condition", "id": item.fhir_id or item.id, "meta": {"versionId": str(item.version)},
        "identifier": [{"system": SOURCE_IDENTIFIER_SYSTEM, "value": item.source_id}],
        "subject": {"reference": f"Patient/{item.patient_id}"}, "code": {"coding": [coding]},
        "recordedDate": _fhir_time(item.recorded_at),
        "extension": [{"url": TERMINOLOGY_STATUS_URL, "valueString": item.terminology_status}],
    }
    if item.clinical_status:
        resource["clinicalStatus"] = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": item.clinical_status}]}
    if item.verification_status:
        resource["verificationStatus"] = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": item.verification_status}]}
    return resource


def report_resource(item: DiagnosticReport) -> dict[str, Any]:
    coding = {"system": item.coding_system or "urn:sentinel:local-report-code", "code": item.code or "unspecified",
              "display": item.display or item.code or "Unspecified diagnostic report"}
    if item.terminology_version:
        coding["version"] = item.terminology_version
    result = [{"reference": f"Observation/{observation_id}"} for observation_id in item.result_observation_ids]
    resource: dict[str, Any] = {
        "resourceType": "DiagnosticReport", "id": item.fhir_id or item.id, "meta": {"versionId": str(item.version)},
        "identifier": [{"system": SOURCE_IDENTIFIER_SYSTEM, "value": item.source_id}], "status": item.status,
        "code": {"coding": [coding]}, "subject": {"reference": f"Patient/{item.patient_id}"}, "result": result,
    }
    if item.effective_at:
        resource["effectiveDateTime"] = _fhir_time(item.effective_at)
    if item.issued_at:
        resource["issued"] = _fhir_time(item.issued_at)
    return resource


def everything_bundle(patient: Patient, observations: list[Observation], conditions: list[Condition], reports: list[DiagnosticReport]) -> dict[str, Any]:
    resources = [patient_resource(patient), *(observation_resource(item) for item in observations),
                 *(condition_resource(item) for item in conditions), *(report_resource(item) for item in reports)]
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": resource} for resource in resources]}


def _patient_from_resource(db: Session, resource: dict[str, Any]) -> Patient:
    patient = db.get(Patient, resource.get("id")) if isinstance(resource.get("id"), str) else None
    if patient is None:
        identifiers = resource.get("identifier", [])
        if isinstance(identifiers, list):
            for identifier in identifiers:
                if isinstance(identifier, dict) and identifier.get("system") == PATIENT_IDENTIFIER_SYSTEM and isinstance(identifier.get("value"), str):
                    patient = db.scalar(select(Patient).where(Patient.reference == identifier["value"]))
                    if patient:
                        break
    if not patient:
        raise FhirValidationError("Patient import only matches an existing synthetic profile by id or Sentinel identifier")
    return patient


def _observation_from_resource(db: Session, user: User, resource: dict[str, Any], full_urls: dict[str, str]) -> tuple[str, Observation | None, str]:
    patient = _patient_from_reference(db, resource.get("subject"), full_urls)
    _authorise_patient(patient, user)
    status = resource.get("status")
    if status not in OBSERVATION_STATUSES:
        raise FhirValidationError("Observation.status is unsupported or missing")
    observed_at = _parse_time(resource.get("effectiveDateTime"), "Observation.effectiveDateTime")
    issued_at = _parse_time(resource.get("issued"), "Observation.issued", required=False) or datetime.now(timezone.utc)
    coding = _coding(resource.get("code"), "Observation.code")
    quantity = _require_object(resource.get("valueQuantity"), "Observation.valueQuantity")
    if quantity.get("system") != UCUM_SYSTEM:
        raise FhirValidationError("Observation.valueQuantity.system must be http://unitsofmeasure.org")
    if not isinstance(quantity.get("value"), (int, float)) or isinstance(quantity.get("value"), bool):
        raise FhirValidationError("Observation.valueQuantity.value must be numeric")
    unit = quantity.get("unit") if isinstance(quantity.get("unit"), str) else quantity.get("code")
    ucum = quantity.get("code") if isinstance(quantity.get("code"), str) else None
    if not isinstance(unit, str) or not unit or not ucum:
        raise FhirValidationError("Observation.valueQuantity requires original unit and UCUM code")
    try:
        validate_ucum_code(ucum)
    except TerminologyError as exc:
        raise FhirValidationError(str(exc)) from exc
    original_code = _extension_string(resource, LOCAL_TEST_CODE_URL)
    try:
        mapping = map_measurement(
            system=coding["system"], code=coding["code"], display=coding["display"], version=coding["version"],
            original_code=original_code, value=float(quantity["value"]), ucum_unit=ucum,
        )
    except TerminologyError as exc:
        raise FhirValidationError(str(exc)) from exc
    source_id, version = _identifier(resource), _version(resource)
    existing = db.scalar(select(Observation).where(Observation.source_id == source_id, Observation.version == version))
    if existing:
        return "duplicate", existing, patient.id
    prior = db.scalar(select(Observation).where(Observation.source_id == source_id).order_by(Observation.version.desc()))
    item = Observation(
        patient_id=patient.id, code=mapping["clinical_key"] or original_code or coding["code"], value=float(quantity["value"]), unit=unit,
        observed_at=observed_at, ingested_at=issued_at, source_id=source_id, version=version,
        is_corrected=prior is not None or status in {"amended", "corrected"}, corrects_observation_id=prior.id if prior else None,
        original_code=mapping["original_code"], coding_system=mapping["coding_system"], standard_code=mapping["standard_code"],
        coding_display=mapping["coding_display"], terminology_version=mapping["terminology_version"], terminology_status=mapping["terminology_status"],
        clinical_key=mapping["clinical_key"], ucum_unit=ucum, normalised_value=mapping["normalised_value"], normalised_ucum_unit=mapping["normalised_ucum_unit"],
        result_status=status, method=("CKD-EPI creatinine-based formula / 1.73 sq M" if mapping["clinical_key"] == "egfr_ckd_epi" else None),
        egfr_equation=("CKD-EPI" if mapping["clinical_key"] == "egfr_ckd_epi" else None), fhir_id=resource.get("id") if isinstance(resource.get("id"), str) else None,
        provenance={"format": "FHIR R4", "fhir_version": FHIR_VERSION, "conversion": mapping["conversion_provenance"], "original_quantity": {"value": quantity["value"], "unit": unit, "ucum_code": ucum}},
    )
    db.add(item)
    db.flush()
    return "imported", item, patient.id


def _condition_from_resource(db: Session, user: User, resource: dict[str, Any], full_urls: dict[str, str]) -> tuple[str, Condition | None, str]:
    patient = _patient_from_reference(db, resource.get("subject"), full_urls)
    _authorise_patient(patient, user)
    coding = _coding(resource.get("code"), "Condition.code")
    source_id, version = _identifier(resource), _version(resource)
    existing = db.scalar(select(Condition).where(Condition.source_id == source_id, Condition.version == version))
    if existing:
        return "duplicate", existing, patient.id
    clinical = _coding(resource.get("clinicalStatus"), "Condition.clinicalStatus", required=False)
    verification = _coding(resource.get("verificationStatus"), "Condition.verificationStatus", required=False)
    known = verified_condition(coding["system"], coding["code"])
    item = Condition(
        patient_id=patient.id, source_id=source_id, version=version,
        clinical_status=clinical["code"] if clinical else None, verification_status=verification["code"] if verification else None,
        source_coding_system=coding["system"], source_code=coding["code"], source_display=coding["display"], source_terminology_version=coding["version"],
        snomed_code=coding["code"] if known else None, snomed_display=known["display"] if known else None,
        snomed_version=coding["version"] if known else None, terminology_status="verified" if known else "needs_terminology_review",
        recorded_at=_parse_time(resource.get("recordedDate"), "Condition.recordedDate"),
        fhir_id=resource.get("id") if isinstance(resource.get("id"), str) else None,
        provenance={"format": "FHIR R4", "fhir_version": FHIR_VERSION, "source_code_preserved": True},
    )
    db.add(item)
    db.flush()
    return "imported", item, patient.id


def _report_observations(db: Session, patient: Patient, results: Any, full_urls: dict[str, str]) -> list[str]:
    if not isinstance(results, list):
        raise FhirValidationError("DiagnosticReport.result must be an array in this subset")
    ids: list[str] = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("reference"), str):
            raise FhirValidationError("DiagnosticReport.result references must be Observation references")
        reference = result["reference"]
        if reference in full_urls:
            reference = f"Observation/{full_urls[reference]}"
        if not reference.startswith("Observation/"):
            raise FhirValidationError("DiagnosticReport.result references must target Observation")
        external_id = reference.removeprefix("Observation/")
        observation = db.get(Observation, external_id) or db.scalar(select(Observation).where(Observation.fhir_id == external_id))
        if not observation or observation.patient_id != patient.id:
            raise FhirValidationError("DiagnosticReport result observation does not resolve within the patient")
        ids.append(observation.id)
    return ids


def _report_from_resource(db: Session, user: User, resource: dict[str, Any], full_urls: dict[str, str]) -> tuple[str, DiagnosticReport | None, str]:
    patient = _patient_from_reference(db, resource.get("subject"), full_urls)
    _authorise_patient(patient, user)
    status = resource.get("status")
    if status not in REPORT_STATUSES:
        raise FhirValidationError("DiagnosticReport.status is unsupported or missing")
    coding = _coding(resource.get("code"), "DiagnosticReport.code")
    source_id, version = _identifier(resource), _version(resource)
    existing = db.scalar(select(DiagnosticReport).where(DiagnosticReport.source_id == source_id, DiagnosticReport.version == version))
    if existing:
        return "duplicate", existing, patient.id
    item = DiagnosticReport(
        patient_id=patient.id, source_id=source_id, version=version, status=status,
        coding_system=coding["system"], code=coding["code"], display=coding["display"], terminology_version=coding["version"],
        effective_at=_parse_time(resource.get("effectiveDateTime"), "DiagnosticReport.effectiveDateTime", required=False),
        issued_at=_parse_time(resource.get("issued"), "DiagnosticReport.issued", required=False),
        result_observation_ids=_report_observations(db, patient, resource.get("result", []), full_urls),
        fhir_id=resource.get("id") if isinstance(resource.get("id"), str) else None,
        provenance={"format": "FHIR R4", "fhir_version": FHIR_VERSION},
    )
    db.add(item)
    db.flush()
    return "imported", item, patient.id


def import_bundle(db: Session, user: User, bundle: Any) -> dict[str, Any]:
    body = _require_object(bundle, "FHIR request")
    if body.get("resourceType") != "Bundle" or body.get("type") != "collection":
        raise FhirValidationError("This MVP accepts only a FHIR R4 Bundle with type 'collection'")
    entries = body.get("entry")
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise FhirValidationError("Bundle.entry must contain 1 to 100 resources")
    if len(json.dumps(body)) > 1_000_000:
        raise FhirValidationError("FHIR Bundle is too large for this MVP")
    resources: list[tuple[dict[str, Any], str | None]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise FhirValidationError("Each Bundle.entry must be an object")
        resource = _require_object(entry.get("resource"), "Bundle.entry.resource")
        resource_type = resource.get("resourceType")
        if resource_type not in {"Patient", "Observation", "Condition", "DiagnosticReport"}:
            raise FhirValidationError(f"Unsupported FHIR resourceType {resource_type!r}")
        resources.append((resource, entry.get("fullUrl") if isinstance(entry.get("fullUrl"), str) else None))

    full_urls: dict[str, str] = {}
    result: list[dict[str, str]] = []
    affected_patients: set[str] = set()
    # Resolve/match patients first so later resources may use their entry fullUrl.
    for resource, full_url in resources:
        if resource["resourceType"] != "Patient":
            continue
        patient = _patient_from_resource(db, resource)
        _authorise_patient(patient, user)
        if full_url:
            full_urls[full_url] = patient.id
        result.append({"resourceType": "Patient", "id": patient.id, "status": "matched"})
    for resource, full_url in resources:
        if resource["resourceType"] != "Observation":
            continue
        status, item, patient_id = _observation_from_resource(db, user, resource, full_urls)
        if item and full_url:
            full_urls[full_url] = item.id
        result.append({"resourceType": "Observation", "id": item.id if item else "", "status": status})
        if status == "imported":
            affected_patients.add(patient_id)
    for resource, _ in resources:
        if resource["resourceType"] != "Condition":
            continue
        status, item, patient_id = _condition_from_resource(db, user, resource, full_urls)
        result.append({"resourceType": "Condition", "id": item.id if item else "", "status": status})
        if status == "imported":
            affected_patients.add(patient_id)
    for resource, _ in resources:
        if resource["resourceType"] != "DiagnosticReport":
            continue
        status, item, patient_id = _report_from_resource(db, user, resource, full_urls)
        result.append({"resourceType": "DiagnosticReport", "id": item.id if item else "", "status": status})
        if status == "imported":
            affected_patients.add(patient_id)
    return {"fhir_version": FHIR_VERSION, "data_label": "Synthetic data · demonstration only", "results": result,
            "reprocess_patient_ids": sorted(affected_patients), "reprocess_required": bool(affected_patients)}
