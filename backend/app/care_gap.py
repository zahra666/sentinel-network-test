"""SGLT2-inhibitor medication-list review for the synthetic T2DM cohort.

Finds records that meet a guideline organ-protection criterion (ASCVD, or a
CKD-EPI eGFR below 60) and have no active, verified SGLT2-inhibitor entry. The
output is a clinician review list: it never recommends a drug for an
individual, places an order, triages or contacts anyone. Evidence that cannot
be verified (unmapped medication codes, missing or incompatible eGFR) is
reported as needing review instead of being treated as a gap.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .guideline_tree import _effective_source_versions
from .models import AuditEvent, Condition, MedicationStatement, Observation, Organization, Patient, User
from .terminology import ASCVD_SNOMED_CODES, SGLT2_ATC_GROUP, SNOMED_CONDITIONS, T2DM_SNOMED_CODE, compatible_for_rule

RULES = json.loads((Path(__file__).parent / "rules" / "sglt2_care_gap_rules.json").read_text(encoding="utf-8"))
CKD_EGFR_BELOW = RULES["criteria"]["ckd_egfr_below"]
INITIATION_EGFR_MINIMUM = RULES["criteria"]["initiation_egfr_minimum"]
MAX_LIMIT = 50
LISTED_STATES = ("review_candidate", "below_initiation_threshold", "medication_list_needs_review", "insufficient_evidence")
NOTICE = ("Clinician review list on synthetic data. A listed record met a guideline criterion and has no active, "
          "verified SGLT2-inhibitor entry; it is not a prescription or an individual treatment recommendation.")


def _active_confirmed(item: Condition) -> bool:
    return item.terminology_status == "verified" and item.clinical_status == "active" and item.verification_status == "confirmed"


def _latest_medications(items: list[MedicationStatement]) -> list[MedicationStatement]:
    latest: dict[str, MedicationStatement] = {}
    for item in items:
        existing = latest.get(item.source_id)
        if existing is None or (item.version, item.recorded_at, item.id) > (existing.version, existing.recorded_at, existing.id):
            latest[item.source_id] = item
    return list(latest.values())


def evaluate_patient(patient: Patient, conditions: list[Condition], observations: list[Observation], medications: list[MedicationStatement]) -> dict:
    """Return the review state and the evidence behind it for one synthetic record."""
    confirmed = [item for item in conditions if _active_confirmed(item)]
    has_t2dm = any(item.snomed_code == T2DM_SNOMED_CODE for item in confirmed)
    ascvd = [item for item in confirmed if item.snomed_code in ASCVD_SNOMED_CODES]

    egfr_items = sorted((item for item in _effective_source_versions(observations) if item.code == "egfr" or item.clinical_key == "egfr_ckd_epi"), key=lambda item: item.observed_at)
    latest_egfr = egfr_items[-1] if egfr_items else None
    egfr_valid = latest_egfr is not None and compatible_for_rule(latest_egfr, "egfr_ckd_epi")
    egfr_value = latest_egfr.normalised_value if egfr_valid else None

    active_meds = [item for item in _latest_medications(medications) if item.status == "active"]
    on_sglt2 = [item for item in active_meds if item.terminology_status == "verified" and (item.atc_code or "").startswith(SGLT2_ATC_GROUP)]
    unmapped = [item for item in active_meds if item.terminology_status != "verified"]

    criteria = [{"criterion": "ascvd", "condition": SNOMED_CONDITIONS[item.snomed_code]["display"], "snomed_code": item.snomed_code} for item in ascvd]
    if egfr_value is not None and egfr_value < CKD_EGFR_BELOW:
        criteria.append({"criterion": "egfr_below_60", "egfr": egfr_value})

    evidence = {
        "latest_egfr": ({"value": latest_egfr.value, "unit": latest_egfr.unit, "normalised_value": latest_egfr.normalised_value,
                         "equation": latest_egfr.egfr_equation, "terminology_status": latest_egfr.terminology_status,
                         "observed_at": latest_egfr.observed_at.isoformat(), "source_id": latest_egfr.source_id} if latest_egfr else None),
        "active_medications": [{"source_id": item.source_id, "atc_code": item.atc_code, "display": item.atc_display or item.source_display,
                                "terminology_status": item.terminology_status} for item in active_meds],
    }

    if not has_t2dm or patient.pregnancy_related:
        state, detail = "out_of_scope", "Not a confirmed T2DM record, or pregnancy-related (outside this review)."
    elif on_sglt2 and criteria:
        state, detail = "on_sglt2_inhibitor", "Criterion met; an active, verified SGLT2-inhibitor entry is present."
    elif not criteria and not ascvd and egfr_value is None:
        state, detail = "insufficient_evidence", "No ASCVD condition and no comparable CKD-EPI eGFR; the criterion cannot be assessed."
    elif not criteria:
        state, detail = "criterion_not_met", "No ASCVD condition and eGFR is 60 or above."
    elif unmapped:
        state, detail = "medication_list_needs_review", "Criterion met, but the medication list contains an unmapped entry, so SGLT2-inhibitor use cannot be ruled out."
    elif egfr_value is None:
        state, detail = "insufficient_evidence", "ASCVD criterion met, but no comparable CKD-EPI eGFR is available to check the initiation threshold."
    elif egfr_value < INITIATION_EGFR_MINIMUM:
        state, detail = "below_initiation_threshold", f"Criterion met, but eGFR is below {INITIATION_EGFR_MINIMUM}; guideline initiation threshold not met. Clinician review."
    else:
        state, detail = "review_candidate", "Criterion met and no active, verified SGLT2-inhibitor entry. Listed for clinician medication review."
    return {"state": state, "detail": detail, "criteria_met": criteria, "evidence": evidence}


def sglt2_care_gaps(db: Session, organisation_id: str | None, limit: int = 20) -> dict:
    """Review the cohort inside one organisation boundary (None = all, administrator/CAIO only)."""
    if not isinstance(limit, int) or limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    query = select(Patient).order_by(Patient.reference)
    if organisation_id:
        query = query.where(Patient.organization_id == organisation_id)
    patients = list(db.scalars(query).all())
    ids = [item.id for item in patients]
    by_patient: dict[str, dict[str, list]] = {pid: {"conditions": [], "observations": [], "medications": []} for pid in ids}
    for model, key in ((Condition, "conditions"), (Observation, "observations"), (MedicationStatement, "medications")):
        for item in db.scalars(select(model).where(model.patient_id.in_(ids))).all():
            by_patient[item.patient_id][key].append(item)
    organisations = {item.id: item.code for item in db.scalars(select(Organization)).all()}

    counts: dict[str, int] = {}
    rows: list[dict] = []
    for patient in patients:
        data = by_patient[patient.id]
        result = evaluate_patient(patient, data["conditions"], data["observations"], data["medications"])
        counts[result["state"]] = counts.get(result["state"], 0) + 1
        if result["state"] in LISTED_STATES:
            rows.append({"patient_id": patient.id, "patient_reference": patient.reference, "organisation": organisations.get(patient.organization_id),
                         "care_setting": patient.care_setting, **result})
    rows.sort(key=lambda row: (LISTED_STATES.index(row["state"]), row["patient_reference"]))
    return {
        "data_label": "Synthetic data — demonstration only",
        "rule_version": RULES["version"],
        "references": RULES["references"],
        "scope": "organisation" if organisation_id else "system",
        "reviewed_records": len(patients),
        "counts": counts,
        "returned": min(len(rows), limit),
        "total_listed": len(rows),
        "maximum": MAX_LIMIT,
        "records": rows[:limit],
        "notice": NOTICE,
    }


def care_gaps_for_user(db: Session, user: User, limit: int = 20) -> dict:
    """Browser/agent entry point: administrators see all hospitals, clinicians their own only."""
    if user.role == "clinician":
        boundary = user.organization_id
        if not boundary:
            raise PermissionError("Clinician account has no organisation")
    elif user.role == "administrator":
        boundary = None
    else:
        raise PermissionError("Only administrators and clinicians can run the SGLT2 medication-list review")
    result = sglt2_care_gaps(db, boundary, limit)
    db.add(AuditEvent(actor_id=user.id, event_type="care_gap.sglt2_reviewed", entity_type="cohort", entity_id=boundary or "all",
                      detail={"rule_version": result["rule_version"], "returned": result["returned"], "counts": result["counts"]}))
    return result
