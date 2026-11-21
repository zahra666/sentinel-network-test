"""Complication radar: per-patient screening status for the four diabetes complication domains.

Clinician decision support only. The radar summarises how recently each domain was
screened and whether a recorded result or trend deserves review. It never diagnoses,
prescribes, triages, submits a referral or contacts a patient. "Stage" actions only
write an audit event so a clinician can sign the order off in their own system.

Data sources:
- ``observations``: the CKD-EPI eGFR results already in the record (the renal profile
  panels plus the current result), using effective source versions only. The kidney
  trend is drawn from exactly the values shown in the Renal profile tab.
- ``care_events``: synthetic annual-review history (eye and foot exams, urine ACR,
  LDL cholesterol, blood pressure). These rows are kept out of the source batch,
  FHIR export and the deterministic review tree. Blood pressure follows the recorded
  hypertension diagnosis and urine ACR follows the latest eGFR, so the radar agrees
  with the rest of the record.

Thresholds are illustrative demo values for synthetic data, not clinical guidance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .guideline_tree import _effective_source_versions
from .models import AuditEvent, CareEvent, Condition, Observation, Patient, User
from .terminology import HYPERTENSION_SNOMED_CODE
from .security import require_roles

router = APIRouter(prefix="/api/patients", tags=["complications"])
staff = require_roles("administrator", "clinician")

SCREENING_INTERVAL_DAYS = 365
EGFR_THRESHOLD = 60.0          # illustrative demo boundary for the kidney trend
PROJECTION_WINDOW_MONTHS = 24
UACR_REVIEW_MG_PER_G = 30.0    # illustrative demo threshold
LDL_TARGET_MMOL = 2.6          # illustrative demo threshold
SYSTOLIC_TARGET = 140.0        # illustrative demo threshold
SYNTHETIC = {"source": "deterministic synthetic care history", "synthetic_only": True}

ACTIONS = {
    "kidney": ("uacr_order", "Stage urine ACR order"),
    "eye": ("retinal_screening", "Stage retinal screening"),
    "foot": ("foot_exam", "Stage monofilament foot exam"),
    "heart": ("lipid_review", "Stage lipid and BP review"),
}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _months_since(when: datetime, now: datetime) -> int:
    return max(0, round((now - _utc(when)).days / 30.44))


# ---------------------------------------------------------------- synthetic history

def _latest_egfr(observations: list[Observation]) -> float | None:
    values = [item for item in observations if item.clinical_key == "egfr_ckd_epi" and item.normalised_value is not None]
    return float(max(values, key=lambda item: _utc(item.observed_at)).normalised_value) if values else None


def seed_care_history(db: Session, anchor: datetime) -> int:
    """Create deterministic synthetic history once. Safe to call on every start."""
    if db.scalar(select(CareEvent.id).limit(1)):
        return 0
    patients = list(db.scalars(select(Patient).order_by(Patient.reference)).all())
    hypertensive = set(db.scalars(select(Condition.patient_id).where(
        Condition.snomed_code == HYPERTENSION_SNOMED_CODE, Condition.clinical_status == "active")).all())
    egfr_rows = list(db.scalars(select(Observation).where(Observation.clinical_key == "egfr_ckd_epi")).all())
    by_patient: dict[str, list[Observation]] = {}
    for item in egfr_rows:
        by_patient.setdefault(item.patient_id, []).append(item)
    events: list[CareEvent] = []
    for patient in patients:
        n = int(patient.reference.rsplit("-", 1)[-1])
        day = lambda months: anchor - timedelta(days=round(months * 30.44))  # noqa: E731
        add = lambda kind, months, **kw: events.append(CareEvent(patient_id=patient.id, kind=kind, performed_at=day(months), provenance=dict(SYNTHETIC), **kw))  # noqa: E731

        egfr = _latest_egfr(_effective_source_versions(by_patient.get(patient.id, [])))
        # Albuminuria is commoner when kidney function is already reduced.
        raised_acr = (egfr is not None and egfr < 60 and n % 3 != 0) or n % 7 == 0
        add("uacr", 16 + n % 5 if n % 3 == 0 else 3 + n % 6, value=float(48 + n % 40) if raised_acr else float(8 + n % 15), unit="mg/g")
        if n % 10 != 5:
            add("retinal_exam", 14 + n % 12 if n % 4 == 1 else 2 + n % 8, result="background_retinopathy" if n % 7 == 0 else "no_retinopathy")
        add("foot_exam", 13 + n % 9 if n % 5 == 2 else 1 + n % 9, result="reduced_sensation" if n % 6 == 0 else "normal")
        add("ldl", 4 + n % 7, value=3.4 if n % 4 == 0 else 2.1 + (n % 4) * 0.1, unit="mmol/L")
        # Blood pressure follows the recorded hypertension diagnosis: some treated patients
        # remain above target, people without the diagnosis read normal.
        if patient.id in hypertensive:
            systolic, diastolic = (146.0 + n % 12, 86.0 + n % 6) if n % 3 == 0 else (128.0 + n % 10, 78.0 + n % 6)
        else:
            systolic, diastolic = 112.0 + n % 14, 70.0 + n % 8
        add("blood_pressure", 1 + n % 3, value=systolic, value_secondary=diastolic, unit="mmHg")
    db.add_all(events)
    return len(events)


# ---------------------------------------------------------------- domain logic

def _latest(events: list[CareEvent], kind: str) -> CareEvent | None:
    found = [event for event in events if event.kind == kind]
    return max(found, key=lambda event: _utc(event.performed_at)) if found else None


def _recency(event: CareEvent | None, now: datetime) -> tuple[str, str]:
    if not event:
        return "not_recorded", "No screening on record"
    months = _months_since(event.performed_at, now)
    if (now - _utc(event.performed_at)).days > SCREENING_INTERVAL_DAYS:
        return "overdue", f"Overdue by {max(1, months - 12)} month{'s' if months - 12 != 1 else ''}"
    return "up_to_date", f"Screened {months} month{'s' if months != 1 else ''} ago" if months else "Screened this month"


def kidney_trend(events: list[CareEvent], observations: list[Observation], now: datetime) -> dict[str, Any] | None:
    effective = _effective_source_versions(observations)
    points = [(_utc(item.observed_at), float(item.normalised_value)) for item in effective
              if item.clinical_key == "egfr_ckd_epi" and item.normalised_value is not None]
    points.sort()
    if len(points) < 3:
        return None
    t0 = points[0][0]
    xs = [(when - t0).days / 365.25 for when, _ in points]
    ys = [value for _, value in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs) or 1.0
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator   # mL/min per year
    last_when, last_value = points[-1]
    months_to_threshold = None
    projection = []
    if slope < 0:
        if last_value > EGFR_THRESHOLD:
            months = round((last_value - EGFR_THRESHOLD) / (-slope) * 12)
            # Only report a crossing inside the projection window; further out it is noise.
            months_to_threshold = months if months <= PROJECTION_WINDOW_MONTHS else None
        for step in range(0, PROJECTION_WINDOW_MONTHS + 1, 3):
            projection.append({"date": (last_when + timedelta(days=round(step * 30.44))).date().isoformat(), "value": round(last_value + slope * step / 12, 1)})
    return {
        "points": [{"date": when.date().isoformat(), "value": round(value, 1)} for when, value in points],
        "projection": projection,
        "threshold": EGFR_THRESHOLD,
        "slope_per_year": round(slope, 1),
        "latest": round(last_value, 1),
        "months_to_threshold": months_to_threshold,
        "method": "Least-squares linear trend over the CKD-EPI eGFR results in the record (renal profile)",
    }


def build_radar(patient: Patient, events: list[CareEvent], observations: list[Observation], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    trend = kidney_trend(events, observations, now)
    domains: list[dict[str, Any]] = []

    # Kidney
    uacr = _latest(events, "uacr")
    status, headline = _recency(uacr, now)
    metrics = []
    if trend:
        metrics.append({"label": "eGFR", "value": f"{trend['latest']:g}", "unit": "mL/min/1.73m²"})
    if uacr:
        metrics.append({"label": "Urine ACR", "value": f"{uacr.value:g}", "unit": "mg/g"})
    if uacr and uacr.value is not None and uacr.value >= UACR_REVIEW_MG_PER_G:
        status, headline = "needs_review", "Raised urine ACR"
    if trend and (trend["latest"] < EGFR_THRESHOLD or (trend["months_to_threshold"] is not None and trend["months_to_threshold"] <= PROJECTION_WINDOW_MONTHS)):
        status = "needs_review"
        headline = "eGFR below 60" if trend["latest"] < EGFR_THRESHOLD else f"eGFR trend reaches 60 in about {trend['months_to_threshold']} months"
    domains.append({"key": "kidney", "label": "Kidneys", "condition": "Diabetic nephropathy", "status": status, "headline": headline,
                    "last_screened_at": uacr.performed_at.date().isoformat() if uacr else None, "metrics": metrics})

    # Eye
    eye = _latest(events, "retinal_exam")
    status, headline = _recency(eye, now)
    if eye and eye.result == "background_retinopathy":
        status, headline = "needs_review", "Retinopathy recorded at last exam"
    domains.append({"key": "eye", "label": "Eyes", "condition": "Diabetic retinopathy", "status": status, "headline": headline,
                    "last_screened_at": eye.performed_at.date().isoformat() if eye else None,
                    "metrics": [{"label": "Last retinal exam", "value": "Retinopathy" if eye.result == "background_retinopathy" else "No retinopathy", "unit": ""}] if eye else []})

    # Foot
    foot = _latest(events, "foot_exam")
    status, headline = _recency(foot, now)
    if foot and foot.result == "reduced_sensation":
        status, headline = "needs_review", "Reduced sensation at last exam"
    domains.append({"key": "foot", "label": "Feet and nerves", "condition": "Peripheral neuropathy", "status": status, "headline": headline,
                    "last_screened_at": foot.performed_at.date().isoformat() if foot else None,
                    "metrics": [{"label": "Monofilament", "value": "Reduced" if foot.result == "reduced_sensation" else "Normal", "unit": ""}] if foot else []})

    # Heart
    ldl, bp = _latest(events, "ldl"), _latest(events, "blood_pressure")
    status, headline = _recency(ldl, now)
    metrics = []
    if ldl:
        metrics.append({"label": "LDL", "value": f"{ldl.value:g}", "unit": "mmol/L"})
    if bp:
        metrics.append({"label": "Blood pressure", "value": f"{bp.value:g}/{bp.value_secondary:g}", "unit": "mmHg"})
    above = [label for label, flag in (("LDL", ldl and ldl.value is not None and ldl.value > LDL_TARGET_MMOL), ("blood pressure", bp and bp.value is not None and bp.value >= SYSTOLIC_TARGET)) if flag]
    if above:
        joined = " and ".join(above)
        status, headline = "needs_review", f"{joined[0].upper()}{joined[1:]} above target"
    domains.append({"key": "heart", "label": "Heart", "condition": "Cardiovascular risk", "status": status, "headline": headline,
                    "last_screened_at": ldl.performed_at.date().isoformat() if ldl else None, "metrics": metrics})

    for domain in domains:
        key, label = ACTIONS[domain["key"]]
        domain["action"] = {"key": key, "label": label} if domain["status"] != "up_to_date" else None
    order = {"needs_review": 0, "overdue": 1, "not_recorded": 2, "up_to_date": 3}
    return {
        "patient_id": patient.id,
        "reference": patient.reference,
        "generated_at": now.isoformat(),
        "domains": domains,
        "summary": {state: sum(1 for d in domains if d["status"] == state) for state in order},
        "kidney_trend": trend,
        "data_label": "Synthetic data · Demonstration only",
    }


# ---------------------------------------------------------------- routes

def _authorised(patient_id: str, user: User, db: Session) -> Patient:
    patient = db.get(Patient, patient_id)
    if not patient or (user.role == "clinician" and user.organization_id != patient.organization_id):
        raise HTTPException(status_code=404, detail="Patient record not found")
    return patient


def _load(db: Session, patient: Patient) -> tuple[list[CareEvent], list[Observation]]:
    events = list(db.scalars(select(CareEvent).where(CareEvent.patient_id == patient.id)).all())
    observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id)).all())
    return events, observations


@router.get("/{patient_id}/complication-radar")
def complication_radar(patient_id: str, user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    patient = _authorised(patient_id, user, db)
    return build_radar(patient, *_load(db, patient))


@router.post("/{patient_id}/complication-radar/{domain}/stage")
def stage_action(patient_id: str, domain: str, user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    """Record that a clinician pre-staged an order. Nothing is ordered, sent or referred."""
    if domain not in ACTIONS:
        raise HTTPException(status_code=404, detail="Unknown complication domain")
    patient = _authorised(patient_id, user, db)
    key, label = ACTIONS[domain]
    db.add(AuditEvent(actor_id=user.id, event_type="care_gap.order_staged", entity_type="patient", entity_id=patient.id,
                      detail={"domain": domain, "action": key, "submitted": False, "synthetic_only": True}))
    db.commit()
    item = label.removeprefix("Stage ")
    return {"status": "staged", "domain": domain, "action": key, "message": f"{item[:1].upper()}{item[1:]} staged for clinician sign-off. Nothing has been sent."}


def cohort_care_gaps(db: Session, user: User) -> dict[str, Any]:
    """Aggregate radar statuses across the patients the user may see."""
    query = select(Patient)
    if user.role == "clinician":
        query = query.where(Patient.organization_id == user.organization_id)
    patients = list(db.scalars(query).all())
    ids = [patient.id for patient in patients]
    events = list(db.scalars(select(CareEvent).where(CareEvent.patient_id.in_(ids))).all()) if ids else []
    observations = list(db.scalars(select(Observation).where(Observation.patient_id.in_(ids), Observation.clinical_key == "egfr_ckd_epi")).all()) if ids else []
    by_patient_events: dict[str, list[CareEvent]] = {}
    by_patient_obs: dict[str, list[Observation]] = {}
    for event in events:
        by_patient_events.setdefault(event.patient_id, []).append(event)
    for item in observations:
        by_patient_obs.setdefault(item.patient_id, []).append(item)
    now = datetime.now(timezone.utc)
    totals = {key: {"needs_review": 0, "overdue": 0, "not_recorded": 0, "up_to_date": 0} for key in ACTIONS}
    for patient in patients:
        radar = build_radar(patient, by_patient_events.get(patient.id, []), by_patient_obs.get(patient.id, []), now)
        for domain in radar["domains"]:
            totals[domain["key"]][domain["status"]] += 1
    return {"patients": len(patients), "domains": totals}


@router.get("/care-gaps/summary")
def care_gap_summary(user: User = Depends(staff), db: Session = Depends(get_db)) -> dict:
    return cohort_care_gaps(db, user)
