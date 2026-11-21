from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .treatment import cohort_summary, modality_of
from .renal_profile import renal_profile
from .care_gap import care_gaps_for_user
from .agent import chat, enqueue_task, process_pending_tasks, process_task
from .chain import ChainUnavailable, anchor_batch, anchor_research_grant, revoke_research_grant, verify_batch, verify_research_grant
from .config import get_settings
from .db import get_db, initialise_database
from .fhir_r4 import FhirValidationError, condition_resource, everything_bundle, import_bundle, observation_resource, patient_resource, report_resource
from .governance import build_governance_dashboard, review_snapshot
from .guideline_tree import assessment
from .models import AgentMessage, MedicationStatement, Alert, AuditEvent, Condition, DelegatedTask, DiagnosticReport, Finding, GovernanceReview, McpDemoSession, MdtReview, NotificationAttempt, Observation, Organization, Patient, ResearchGrant, Scan, SourceBatch, User
from .mdt import run_three_member_mdt
from .mcp_access import router as mcp_access_router
from .live import router as live_router, start_background_runner
from .outreach import router as outreach_router
from .security import create_token, current_user, require_roles, verify_password
from .services.analyse import analyse_patients
from .services.route import mark_alert, route_findings
from .services.sentinel_agent import advance_notification_workflows
from .services.sync import sync_synthetic_records

app = FastAPI(title="Goola", version="0.1.0", description="Synthetic data — demonstration only. Clinician review required.")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
app.include_router(mcp_access_router)
app.include_router(live_router)
app.include_router(outreach_router)


class FhirR4JsonResponse(JSONResponse):
    media_type = "application/fhir+json"


class LoginPayload(BaseModel):
    email: EmailStr
    # The synthetic hackathon accounts deliberately use the documented five-
    # character demo credential. Hosted deployments must replace these accounts.
    password: str = Field(min_length=5)


class AlertAction(BaseModel):
    action: Literal["acknowledge", "resolve"]


class ChatPayload(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class DelegatePayload(BaseModel):
    task_type: Literal["evidence_reviewer", "regional_analyst", "integrity_verifier", "source_crawler"]
    input_data: dict = Field(default_factory=dict)


class MdtAttestation(BaseModel):
    selected_pathways: list[Literal["endocrinology_review", "bariatric_surgery_consideration", "gp_continuity_consideration"]] = Field(default_factory=list)


class GovernanceReviewPayload(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class McpDemoSessionPayload(BaseModel):
    label: str = Field(default="Synthetic external agent", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9 ._-]+$")


class FhirImportPayload(BaseModel):
    bundle: dict


@app.on_event("startup")
def startup() -> None:
    initialise_database()
    if get_settings().seed_synthetic_data:
        from .seed import seed
        seed()
    start_background_runner()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "data_label": "Synthetic data — demonstration only"}


@app.post("/api/auth/login")
def login(payload: LoginPayload, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid demo credentials")
    return {"access_token": create_token(user), "token_type": "bearer", "user": serialize_user(user)}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)) -> dict:
    return serialize_user(user)


@app.get("/api/overview")
def overview(user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    patient_query = select(Patient)
    if user.role == "clinician":
        patient_query = patient_query.where(Patient.organization_id == user.organization_id)
    patients = list(db.scalars(patient_query).all())
    alert_query = select(Alert)
    if user.role == "clinician":
        alert_query = alert_query.join(Patient, Patient.id == Alert.patient_id).where(Patient.organization_id == user.organization_id)
    alerts = list(db.scalars(alert_query).all())
    latest_scan = db.scalar(select(Scan).order_by(Scan.started_at.desc()))
    batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at.desc()))
    pipeline = latest_scan.events if latest_scan else [{"stage": "sync", "state": "not_run"}, {"stage": "analyse", "state": "not_run"}, {"stage": "route", "state": "not_run"}]
    return {"data_label": "Synthetic data — demonstration only", "metrics": {"monitored_profiles": len(patients), "open_review_alerts": sum(item.status == "new" for item in alerts), "acknowledged_alerts": sum(item.status == "acknowledged" for item in alerts), "integrity": batch.receipt_status if batch else "not_seeded"}, "pipeline": pipeline, "latest_scan": serialize_scan(latest_scan) if latest_scan else None, "recent_alerts": [serialize_alert(item, db) for item in sorted(alerts, key=lambda item: item.id, reverse=True)[:6]]}


@app.post("/api/scans/run")
def run_scan_now(user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    scan = execute_scan(db, trigger="manual", actor=user)
    db.commit()
    return serialize_scan(scan)


@app.post("/api/internal/scheduled-scan")
def scheduled_scan(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict:
    """GitHub Actions trigger for the zero-cost hosted demo; never a browser route."""
    settings = get_settings()
    expected = f"Bearer {settings.scheduler_trigger_token}"
    if not settings.schedule_enabled or not settings.scheduler_trigger_token or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid scheduler trigger")
    scan = execute_scan(db, trigger="github_schedule")
    sentinel = advance_notification_workflows(db)
    delegated = process_pending_tasks(db)
    db.add(AuditEvent(
        event_type="scheduler.github_triggered",
        entity_type="scheduler",
        entity_id="github-actions",
        detail={"mode": settings.scheduler_mode, "sentinel": sentinel, "delegated_tasks": delegated},
    ))
    db.commit()
    return {"status": "completed", "scan": serialize_scan(scan), "sentinel": sentinel, "delegated_tasks": delegated}


def execute_scan(db: Session, trigger: str, actor: User | None = None) -> Scan:
    scan = Scan(trigger=trigger, events=[])
    db.add(scan)
    db.flush()
    records, sync_event = sync_synthetic_records(db)
    analyses, analyse_event = analyse_patients(db)
    created, route_event = route_findings(db, scan.id, analyses)
    scan.scanned_count = len({record.patient_id for record in records})
    scan.finding_count = created
    scan.skipped_count = max(0, len(analyses) - created)
    scan.events = [sync_event, analyse_event, route_event]
    scan.status = "completed"
    scan.finished_at = datetime.now(timezone.utc)
    db.add(AuditEvent(actor_id=actor.id if actor else None, event_type="scan.completed", entity_type="scan", entity_id=scan.id, detail={"trigger": trigger, "new_findings": created}))
    return scan


@app.get("/api/scans")
def scans(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [serialize_scan(item) for item in db.scalars(select(Scan).order_by(Scan.started_at.desc())).all()]


@app.get("/api/patients")
def patients(user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> list[dict]:
    query = select(Patient).order_by(Patient.reference)
    if user.role == "clinician":
        query = query.where(Patient.organization_id == user.organization_id)
    rows = list(db.scalars(query).all())
    therapy = _treatment_by_patient(db, [patient.id for patient in rows])
    return [{**serialize_patient(patient, db), "treatment": _treatment_brief(therapy[patient.id])} for patient in rows]


def _treatment_by_patient(db: Session, patient_ids: list[str]) -> dict[str, dict]:
    grouped: dict[str, list] = {pid: [] for pid in patient_ids}
    if patient_ids:
        for item in db.scalars(select(MedicationStatement).where(MedicationStatement.patient_id.in_(patient_ids))).all():
            grouped[item.patient_id].append(item)
    return {pid: modality_of(items) for pid, items in grouped.items()}


def _treatment_brief(result: dict) -> dict:
    return {"modality": result["modality"], "label": result["label"], "insulin": result["insulin"], "oral_agent_count": result["oral_agent_count"]}


@app.get("/api/treatment/summary")
def treatment_summary(user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    """Cohort glucose-lowering modality mix next to NDR 2023 (clinicians: own hospital only)."""
    query = select(Patient.id)
    if user.role == "clinician":
        query = query.where(Patient.organization_id == user.organization_id)
    ids = list(db.scalars(query).all())
    return {"scope": "organisation" if user.role == "clinician" else "all_organisations", **cohort_summary(list(_treatment_by_patient(db, ids).values()))}


@app.get("/api/patients/{patient_id}")
def patient_detail(patient_id: str, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    patient = authorised_patient(patient_id, user, db)
    observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at)).all())
    conditions = list(db.scalars(select(Condition).where(Condition.patient_id == patient.id).order_by(Condition.recorded_at)).all())
    reports = list(db.scalars(select(DiagnosticReport).where(DiagnosticReport.patient_id == patient.id).order_by(DiagnosticReport.effective_at)).all())
    return {**serialize_patient(patient, db), "observations": [serialize_observation(item) for item in observations],
            "conditions": [serialize_condition(item) for item in conditions], "diagnostic_reports": [serialize_diagnostic_report(item) for item in reports],
            "guideline_assessment": assessment(patient, observations),
            "treatment": modality_of(db.scalars(select(MedicationStatement).where(MedicationStatement.patient_id == patient.id)).all())}


@app.get("/api/patients/{patient_id}/renal-profile")
def patient_renal_profile(patient_id: str, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    """Seven-analyte renal profile series for charting (synthetic; display flags only)."""
    patient = authorised_patient(patient_id, user, db)
    observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at)).all())
    return {"patient_id": patient.id, "reference": patient.reference, **renal_profile(observations)}


@app.get("/api/fhir/r4/Patient/{patient_id}", response_class=FhirR4JsonResponse)
def fhir_patient(patient_id: str, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    return patient_resource(authorised_patient(patient_id, user, db))


@app.get("/api/fhir/r4/Patient/{patient_id}/$everything", response_class=FhirR4JsonResponse)
def fhir_everything(patient_id: str, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    patient = authorised_patient(patient_id, user, db)
    observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at)).all())
    conditions = list(db.scalars(select(Condition).where(Condition.patient_id == patient.id).order_by(Condition.recorded_at)).all())
    reports = list(db.scalars(select(DiagnosticReport).where(DiagnosticReport.patient_id == patient.id).order_by(DiagnosticReport.effective_at)).all())
    return everything_bundle(patient, observations, conditions, reports)


@app.get("/api/fhir/r4/Observation/{observation_id}", response_class=FhirR4JsonResponse)
def fhir_observation(observation_id: str, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    item = db.get(Observation, observation_id)
    if not item:
        raise HTTPException(status_code=404, detail="Observation not found")
    authorised_patient(item.patient_id, user, db)
    return observation_resource(item)


@app.get("/api/fhir/r4/Condition/{condition_id}", response_class=FhirR4JsonResponse)
def fhir_condition(condition_id: str, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    item = db.get(Condition, condition_id)
    if not item:
        raise HTTPException(status_code=404, detail="Condition not found")
    authorised_patient(item.patient_id, user, db)
    return condition_resource(item)


@app.get("/api/fhir/r4/DiagnosticReport/{report_id}", response_class=FhirR4JsonResponse)
def fhir_report(report_id: str, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    item = db.get(DiagnosticReport, report_id)
    if not item:
        raise HTTPException(status_code=404, detail="DiagnosticReport not found")
    authorised_patient(item.patient_id, user, db)
    return report_resource(item)


@app.post("/api/fhir/r4/import")
def fhir_import(payload: FhirImportPayload, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    try:
        outcome = import_bundle(db, user, payload.bundle)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FhirValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if outcome["reprocess_required"]:
        scan = execute_scan(db, trigger="fhir_import", actor=user)
        outcome["scan"] = serialize_scan(scan)
    db.add(AuditEvent(actor_id=user.id, event_type="fhir.r4_imported", entity_type="fhir_bundle", entity_id="synthetic", detail={"resources": len(outcome["results"]), "reprocess_required": outcome["reprocess_required"]}))
    db.commit()
    return outcome


@app.get("/api/alerts")
def alerts(user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> list[dict]:
    query = select(Alert).order_by(Alert.id.desc())
    if user.role == "clinician":
        query = query.join(Patient, Patient.id == Alert.patient_id).where(Patient.organization_id == user.organization_id)
    return [serialize_alert(alert, db) for alert in db.scalars(query).all()]


@app.post("/api/alerts/{alert_id}")
def update_alert(alert_id: str, payload: AlertAction, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    authorised_patient(alert.patient_id, user, db)
    mark_alert(db, alert, user, payload.action)
    db.commit()
    return serialize_alert(alert, db)


@app.get("/api/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    query = select(NotificationAttempt).where(NotificationAttempt.recipient_user_id == user.id).order_by(NotificationAttempt.created_at.desc())
    return [{"id": item.id, "state": item.state, "created_at": item.created_at.isoformat(), "finding_id": item.finding_id, "data_label": "Synthetic data — demonstration only"} for item in db.scalars(query).all()]


@app.get("/api/research/regional")
def regional_research(user: User = Depends(require_roles("administrator", "researcher")), db: Session = Depends(get_db)) -> dict:
    if user.role == "researcher":
        grant = db.scalar(select(ResearchGrant).where(ResearchGrant.user_id == user.id, ResearchGrant.revoked_at.is_(None), ResearchGrant.expires_at > datetime.now(timezone.utc)))
        if not grant:
            raise HTTPException(status_code=403, detail="No active research grant for this fixed aggregate query")
        try:
            if not verify_research_grant(grant, get_settings().chain_researcher_address):
                raise HTTPException(status_code=503, detail="Research is fail-closed until the active synthetic grant is verified on-chain")
        except ChainUnavailable as exc:
            raise HTTPException(status_code=503, detail="Research is fail-closed because the chain grant cannot be verified") from exc
    batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at.desc()))
    if not batch or batch.receipt_status != "confirmed":
        raise HTTPException(status_code=503, detail="Research is fail-closed until a source batch has a verified chain receipt")
    verification = verify_batch(batch, list(db.scalars(select(Observation)).all()))
    if verification["chain_status"] != "verified":
        raise HTTPException(status_code=503, detail="Research is fail-closed because current source integrity is not verified")
    rows = []
    for organization in db.scalars(select(Organization).order_by(Organization.district)).all():
        org_patients = list(db.scalars(select(Patient).where(Patient.organization_id == organization.id)).all())
        eligible = 0
        numerator = 0
        for patient in org_patients:
            patient_observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id)).all())
            result = assessment(patient, patient_observations)
            if result["status"] not in {"data_quality_review", "out_of_scope"}:
                eligible += 1
                numerator += result["status"] in {"evidence_requires_clinician_review", "confirmation_gap"}
        rows.append({"district": organization.district, "eligible_patients": eligible, "review_evidence_patients": numerator, "anomaly_rate_among_monitored_patients": round(numerator / eligible * 100, 1) if eligible else None, "suppressed": eligible < 20})
    return {"metric": "Anomaly rate among monitored patients", "periods": ["2026-09", "2026-10"], "cohort_definition": "Unique synthetic patients with sufficient valid observations for the stated demo rule.", "rows": rows, "integrity": verification}


@app.get("/api/ledger")
def ledger(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [serialize_batch(item) for item in db.scalars(select(SourceBatch).order_by(SourceBatch.created_at.desc())).all()]


@app.post("/api/ledger/{batch_id}/anchor")
def anchor(batch_id: str, user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    batch = db.get(SourceBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Source batch not found")
    events = [{"id": item.id, "type": item.event_type, "entity": item.entity_id} for item in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at)).all()]
    try:
        anchor_batch(batch, events)
    except ChainUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.add(AuditEvent(actor_id=user.id, event_type="ledger.batch_anchored", entity_type="source_batch", entity_id=batch.id, detail={"transaction_hash": batch.transaction_hash}))
    db.commit()
    return serialize_batch(batch)


@app.post("/api/ledger/{batch_id}/verify")
def verify_ledger(batch_id: str, _user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    batch = db.get(SourceBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Source batch not found")
    return verify_batch(batch, list(db.scalars(select(Observation)).all()))


@app.post("/api/admin/inject-abnormal")
def inject_abnormal(user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    patient = db.scalar(select(Patient).where(Patient.reference == "SYN-DM-001"))
    if not patient:
        raise HTTPException(status_code=404, detail="Seed data not found")
    observation = Observation(patient_id=patient.id, code="hba1c", value=8.8, unit="%", observed_at=datetime.now(timezone.utc), ingested_at=datetime.now(timezone.utc), source_id="DEMO-ABNORMAL-001", version=1)
    db.add(observation)
    db.flush()
    db.add(AuditEvent(actor_id=user.id, event_type="demo.abnormal_injected", entity_type="observation", entity_id=observation.id, detail={"patient_reference": patient.reference}))
    db.commit()
    return {"message": "Synthetic observation injected. Run the normal scan to process it.", "patient_reference": patient.reference}


@app.post("/api/admin/tamper/{batch_id}")
def tamper(batch_id: str, user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    batch = db.get(SourceBatch, batch_id)
    if not batch or not batch.manifest:
        raise HTTPException(status_code=404, detail="Batch not found")
    original = next((item for item in batch.manifest if item["value"] is not None), None)
    observation = db.get(Observation, original["id"]) if original else None
    if not observation:
        raise HTTPException(status_code=404, detail="Immutable source record not found")
    observation.value = float(observation.value or 0) + 2.0
    db.add(AuditEvent(actor_id=user.id, event_type="demo.source_tampered", entity_type="observation", entity_id=observation.id, detail={"batch_id": batch.id, "original_batch_immutable": True}))
    db.commit()
    return {"message": "Synthetic source record changed. Verify against the original batch commitment to see the mismatch."}


@app.post("/api/admin/restore/{batch_id}")
def restore(batch_id: str, user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    batch = db.get(SourceBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    restored = 0
    for entry in batch.manifest:
        observation = db.get(Observation, entry["id"])
        if observation and observation.value != entry["value"]:
            observation.value = entry["value"]
            restored += 1
    db.add(AuditEvent(actor_id=user.id, event_type="demo.source_restored", entity_type="source_batch", entity_id=batch.id, detail={"records_restored": restored}))
    db.commit()
    return {"message": "Source data restored without deleting audit history.", "records_restored": restored}


@app.post("/api/research/grants/{grant_id}/revoke")
def revoke_grant(grant_id: str, user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    grant = db.get(ResearchGrant, grant_id)
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    if grant.chain_grant_hash:
        try:
            revoke_research_grant(grant)
        except ChainUnavailable as exc:
            raise HTTPException(status_code=503, detail="Grant revocation is blocked until its chain transaction succeeds") from exc
    grant.revoked_at = datetime.now(timezone.utc)
    db.add(AuditEvent(actor_id=user.id, event_type="research.grant_revoked", entity_type="research_grant", entity_id=grant.id, detail={"chain_enforcement": "on_chain" if grant.chain_grant_hash else "local_demo_only"}))
    db.commit()
    return {"grant_id": grant.id, "status": "revoked"}


@app.post("/api/research/grants/{grant_id}/anchor")
def anchor_grant(grant_id: str, user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    grant = db.get(ResearchGrant, grant_id)
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    if grant.revoked_at:
        raise HTTPException(status_code=409, detail="Revoked grants cannot be anchored")
    try:
        anchor_research_grant(grant, get_settings().chain_researcher_address)
    except ChainUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.add(AuditEvent(actor_id=user.id, event_type="research.grant_anchored", entity_type="research_grant", entity_id=grant.id, detail={"chain_grant_hash": grant.chain_grant_hash, "synthetic_only": True}))
    db.commit()
    return {"grant_id": grant.id, "chain_grant_hash": grant.chain_grant_hash, "status": "anchored"}


@app.get("/api/care-gaps/sglt2")
def sglt2_care_gap_review(limit: int = 20, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    """Read-only medication-list review; clinicians are bounded to their own hospital."""
    try:
        result = care_gaps_for_user(db, user, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return result


@app.post("/api/agent/chat")
def agent_chat(payload: ChatPayload, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    try:
        response = chat(db, user, payload.content, payload.conversation_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    db.commit()
    return response


@app.post("/api/agent/delegate")
def delegate(payload: DelegatePayload, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    try:
        task = enqueue_task(db, user, payload.task_type, payload.input_data)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    db.commit()
    return serialize_task(task)


@app.post("/api/agent/tasks/{task_id}/run")
def run_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    task = db.get(DelegatedTask, task_id)
    if not task or task.requester_id != user.id:
        raise HTTPException(status_code=404, detail="Delegated task not found")
    process_task(db, task)
    db.commit()
    return serialize_task(task)


@app.post("/api/mdt/{patient_id}/review")
def mdt_review(patient_id: str, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    patient = authorised_patient(patient_id, user, db)
    report = run_three_member_mdt(db, patient)
    review = MdtReview(patient_id=patient.id, requested_by=user.id, report=report)
    db.add(review)
    db.flush()
    db.add(AuditEvent(actor_id=user.id, event_type="mdt.review_generated", entity_type="mdt_review", entity_id=review.id, detail={"members": 3, "patient_reference": patient.reference}))
    db.commit()
    return serialize_mdt_review(review)


@app.post("/api/mdt/reviews/{review_id}/attest")
def attest_mdt(review_id: str, payload: MdtAttestation, user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    review = db.get(MdtReview, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="MDT review not found")
    authorised_patient(review.patient_id, user, db)
    allowed = {item["pathway"] for item in review.report["members"][2]["considerations"] if item["pathway"] != "clinician_review_required"}
    if not set(payload.selected_pathways).issubset(allowed):
        raise HTTPException(status_code=400, detail="A pathway may be attested only when it appears as a clinician consideration in this review")
    review.selected_pathways = payload.selected_pathways
    review.clinician_attested_at = datetime.now(timezone.utc)
    review.clinician_attested_by = user.id
    db.add(AuditEvent(actor_id=user.id, event_type="mdt.review_attested", entity_type="mdt_review", entity_id=review.id, detail={"selected_pathways": payload.selected_pathways, "not_a_referral_order": True}))
    db.commit()
    return serialize_mdt_review(review)


@app.get("/api/agent/conversations/{conversation_id}")
def conversation(conversation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    from .models import AgentConversation
    item = db.get(AgentConversation, conversation_id)
    if not item or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [{"role": message.role, "content": message.content, "tool_events": message.tool_events, "created_at": message.created_at.isoformat()} for message in db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == item.id).order_by(AgentMessage.created_at)).all()]


@app.post("/api/sentinel/tick")
def sentinel_tick(user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    outcome = advance_notification_workflows(db)
    db.commit()
    return {"agent": "Goola Agent", "mode": "in-app-only", **outcome}


@app.get("/api/status")
def system_status(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    scan = db.scalar(select(Scan).order_by(Scan.finished_at.desc()))
    batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at.desc()))
    return {"data_label": "Synthetic data — demonstration only", "api": "healthy", "scheduler": {"timezone": get_settings().schedule_timezone, "enabled": get_settings().schedule_enabled, "mode": get_settings().scheduler_mode, "last_scan": serialize_scan(scan) if scan else None}, "sentinel_agent": "in_app_notifications_and_review_queue_only", "chain": batch.receipt_status if batch else "unavailable", "summary_mode": "deterministic templates"}


@app.get("/api/governance")
def governance_dashboard(_user: User = Depends(require_roles("administrator", "caio")), db: Session = Depends(get_db)) -> dict:
    return build_governance_dashboard(db)


@app.post("/api/governance/reviews")
def record_governance_review(payload: GovernanceReviewPayload, user: User = Depends(require_roles("administrator", "caio")), db: Session = Depends(get_db)) -> dict:
    dashboard = build_governance_dashboard(db)
    note = payload.note.strip() if payload.note else None
    review = GovernanceReview(actor_id=user.id, note=note or None, evidence_snapshot=review_snapshot(dashboard))
    db.add(review)
    db.flush()
    db.add(AuditEvent(
        actor_id=user.id,
        event_type="governance.review_attested",
        entity_type="governance_review",
        entity_id=review.id,
        detail={"governance_version": dashboard["governance_version"], "llm_status": dashboard["live_evidence"]["llm_runtime"]["status"], "review_interval_days": dashboard["review_interval_days"]},
    ))
    db.commit()
    return build_governance_dashboard(db)


@app.post("/api/governance/mcp-demo-sessions")
def start_mcp_demo_session(payload: McpDemoSessionPayload, user: User = Depends(require_roles("administrator", "caio")), db: Session = Depends(get_db)) -> dict:
    """Create a visible simulation record only; it never creates a credential or calls an MCP tool."""
    session = McpDemoSession(
        label=payload.label.strip(),
        created_by=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(session)
    db.flush()
    db.add(AuditEvent(
        actor_id=user.id,
        event_type="mcp.demo_session_started",
        entity_type="mcp_demo_session",
        entity_id=session.id,
        detail={"simulation": True, "expires_in_minutes": 5},
    ))
    db.commit()
    return {"id": session.id, "status": "simulated", "expires_at": session.expires_at.isoformat(), "dashboard": build_governance_dashboard(db)}


def authorised_patient(patient_id: str, user: User, db: Session) -> Patient:
    patient = db.get(Patient, patient_id)
    if not patient or (user.role == "clinician" and user.organization_id != patient.organization_id):
        raise HTTPException(status_code=404, detail="Patient record not found")
    return patient


def serialize_user(user: User) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role, "organization_id": user.organization_id}


def serialize_patient(patient: Patient, db: Session) -> dict:
    organization = db.get(Organization, patient.organization_id)
    return {"id": patient.id, "reference": patient.reference, "care_setting": patient.care_setting, "age_group": patient.age_group, "organization": organization.name if organization else None, "district": organization.district if organization else None, "pregnancy_related": patient.pregnancy_related, "body_mass_index": patient.body_mass_index, "documented_complexity": patient.documented_complexity, "data_label": "Synthetic data — demonstration only"}


def serialize_observation(item: Observation) -> dict:
    return {"id": item.id, "code": item.code, "original_code": item.original_code, "value": item.value, "unit": item.unit,
            "coding": {"system": item.coding_system, "code": item.standard_code, "display": item.coding_display, "version": item.terminology_version},
            "terminology_status": item.terminology_status, "ucum_unit": item.ucum_unit, "normalised_value": item.normalised_value,
            "normalised_ucum_unit": item.normalised_ucum_unit, "result_status": item.result_status, "method": item.method,
            "egfr_equation": item.egfr_equation, "corrects_observation_id": item.corrects_observation_id,
            "observed_at": item.observed_at.isoformat(), "ingested_at": item.ingested_at.isoformat(), "source_id": item.source_id,
            "version": item.version, "is_corrected": item.is_corrected, "is_late_arrival": item.is_late_arrival}


def serialize_condition(item: Condition) -> dict:
    return {"id": item.id, "source_id": item.source_id, "version": item.version, "clinical_status": item.clinical_status,
            "verification_status": item.verification_status, "source_coding": {"system": item.source_coding_system, "code": item.source_code,
            "display": item.source_display, "version": item.source_terminology_version}, "snomed": {"code": item.snomed_code,
            "display": item.snomed_display, "version": item.snomed_version}, "terminology_status": item.terminology_status,
            "recorded_at": item.recorded_at.isoformat()}


def serialize_diagnostic_report(item: DiagnosticReport) -> dict:
    return {"id": item.id, "source_id": item.source_id, "version": item.version, "status": item.status,
            "coding": {"system": item.coding_system, "code": item.code, "display": item.display, "version": item.terminology_version},
            "effective_at": item.effective_at.isoformat() if item.effective_at else None,
            "issued_at": item.issued_at.isoformat() if item.issued_at else None, "result_observation_ids": item.result_observation_ids}


def serialize_alert(alert: Alert, db: Session) -> dict:
    patient = db.get(Patient, alert.patient_id)
    finding = db.get(Finding, alert.finding_id)
    return {"id": alert.id, "status": alert.status, "patient": serialize_patient(patient, db) if patient else None, "finding": {"id": finding.id, "category": finding.category, "status": finding.status, "evidence": finding.evidence, "created_at": finding.created_at.isoformat()} if finding else None, "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None, "escalated_at": alert.escalated_at.isoformat() if alert.escalated_at else None}


def serialize_scan(scan: Scan | None) -> dict | None:
    if not scan:
        return None
    return {"id": scan.id, "status": scan.status, "trigger": scan.trigger, "started_at": scan.started_at.isoformat(), "finished_at": scan.finished_at.isoformat() if scan.finished_at else None, "scanned_count": scan.scanned_count, "finding_count": scan.finding_count, "skipped_count": scan.skipped_count, "events": scan.events}


def serialize_batch(batch: SourceBatch) -> dict:
    return {"id": batch.id, "schema_version": batch.schema_version, "commitment": batch.commitment, "receipt_status": batch.receipt_status, "chain_id": batch.chain_id, "contract_address": batch.contract_address, "transaction_hash": batch.transaction_hash, "block_number": batch.block_number, "created_at": batch.created_at.isoformat()}


def serialize_task(task: DelegatedTask) -> dict:
    return {"id": task.id, "task_type": task.task_type, "status": task.status, "input_data": task.input_data, "output_data": task.output_data, "created_at": task.created_at.isoformat(), "completed_at": task.completed_at.isoformat() if task.completed_at else None}


def serialize_mdt_review(review: MdtReview) -> dict:
    return {"id": review.id, "patient_id": review.patient_id, "report": review.report, "selected_pathways": review.selected_pathways, "clinician_attested_at": review.clinician_attested_at.isoformat() if review.clinician_attested_at else None, "requires_clinician_attestation": True}


# The mount is deliberately last: normal dashboard and API routes resolve first,
# while the MCP ASGI app receives only its own /mcp request path.
from .mcp_server import app as protected_mcp_app

app.mount("/", protected_mcp_app, name="mcp")
