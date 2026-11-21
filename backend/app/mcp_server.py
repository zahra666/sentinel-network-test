"""Read-only, role-scoped MCP listener for the synthetic Sentinel MVP."""
from __future__ import annotations

import hashlib
from contextvars import ContextVar

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from starlette.responses import JSONResponse

from .care_gap import sglt2_care_gaps
from .chain import verify_batch
from .config import get_settings
from .db import SessionLocal, initialise_database
from .fhir_r4 import everything_bundle
from .governance import build_governance_dashboard, mcp_activity_summary
from .guideline_tree import assessment
from .mcp_access import valid_key
from .models import AgentAccessKey, Alert, AuditEvent, Condition, DiagnosticReport, Finding, MdtReview, Observation, Organization, Patient, SourceBatch
from .terminology import ATC_INDEX_DATE, ATC_MEDICATIONS, ATC_SYSTEM, CATALOGUE_VERIFIED_ON, LAB_TERMS, LOINC_SYSTEM, LOINC_VERSION, SNOMED_CONDITIONS, SNOMED_SYSTEM, UCUM_SYSTEM

_key_id: ContextVar[str | None] = ContextVar("mcp_key_id", default=None)
settings = get_settings()
mcp = FastMCP(
    "Sentinel Network",
    instructions=(
        "Synthetic data — demonstration only. Read-only tools require an administrator-approved key. "
        "Clinicians are limited to their own organisation. CAIO access is system-wide only for the synthetic demo. "
        "No tool diagnoses, prescribes, triages, refers, contacts patients, changes data, or performs infrastructure actions."
    ),
    stateless_http=True,
    json_response=True,
    max_request_body_size=16384,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[value.strip() for value in settings.mcp_allowed_hosts.split(",") if value.strip()],
        allowed_origins=[value.strip() for value in settings.mcp_allowed_origins.split(",") if value.strip()],
    ),
)


def audit(db, key: AgentAccessKey | None, tool: str, allowed: bool) -> None:
    db.add(AuditEvent(
        actor_id=key.user_id if key else None,
        event_type="mcp.tool_allowed" if allowed else "mcp.tool_denied",
        entity_type="agent_access_key",
        entity_id=key.id if key else "unknown",
        detail={"tool": tool},
    ))
    db.commit()


def authorize(db, scope: str, tool: str):
    key = db.get(AgentAccessKey, _key_id.get()) if _key_id.get() else None
    try:
        owner = valid_key(db, key)
        if scope not in key.scopes:
            raise HTTPException(403)
    except HTTPException:
        audit(db, key, tool, False)
        raise ValueError("Access denied. An approved key with the required read-only scope is needed.") from None
    audit(db, key, tool, True)
    return owner


def authorize_patient(db, tool: str, patient_id: str) -> Patient:
    if not isinstance(patient_id, str) or len(patient_id) > 36:
        raise ValueError("Invalid patient reference")
    key = db.get(AgentAccessKey, _key_id.get()) if _key_id.get() else None
    try:
        owner = valid_key(db, key)
        patient = db.get(Patient, patient_id)
        if not patient:
            raise HTTPException(404)
        if owner.role == "clinician":
            if "clinical:read:organisation" not in key.scopes or owner.organization_id != patient.organization_id:
                raise HTTPException(403)
        elif owner.role == "caio":
            if "clinical:read:system" not in key.scopes:
                raise HTTPException(403)
        else:
            raise HTTPException(403)
    except HTTPException:
        audit(db, key, tool, False)
        raise ValueError("Access denied. The patient is outside the approved read-only access profile.") from None
    audit(db, key, tool, True)
    return patient


def authorize_clinical_listing(db, tool: str):
    """Return a role-scoped clinical query boundary without exposing a bulk export."""
    key = db.get(AgentAccessKey, _key_id.get()) if _key_id.get() else None
    try:
        owner = valid_key(db, key)
        if owner.role == "clinician" and "clinical:read:organisation" in key.scopes:
            boundary = owner.organization_id
        elif owner.role == "caio" and "clinical:read:system" in key.scopes:
            boundary = None
        else:
            raise HTTPException(403)
    except HTTPException:
        audit(db, key, tool, False)
        raise ValueError("Access denied. The requested clinical view is outside the approved read-only access profile.") from None
    audit(db, key, tool, True)
    return boundary


def patient_summary(patient: Patient, db) -> dict:
    organisation = db.get(Organization, patient.organization_id)
    observations = list(db.scalars(
        select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at.desc(), Observation.id).limit(101)
    ).all())
    conditions = list(db.scalars(
        select(Condition).where(Condition.patient_id == patient.id).order_by(Condition.recorded_at.desc())
    ).all())
    reports = list(db.scalars(
        select(DiagnosticReport).where(DiagnosticReport.patient_id == patient.id).order_by(DiagnosticReport.effective_at.desc())
    ).all())
    return {
        "id": patient.id,
        "reference": patient.reference,
        "organisation": organisation.name if organisation else None,
        "care_setting": patient.care_setting,
        "age_group": patient.age_group,
        "pregnancy_related": patient.pregnancy_related,
        "data_label": "Synthetic data — demonstration only",
        "review_notice": "Clinician review requested; no diagnosis, treatment instruction, urgency classification, or referral order.",
        "observations_truncated": len(observations) > 100,
        "observations": [
            {
                "id": item.id,
                "code": item.code,
                "original_code": item.original_code,
                "value": item.value,
                "unit": item.unit,
                "ucum_unit": item.ucum_unit,
                "coding": {"system": item.coding_system, "code": item.standard_code, "display": item.coding_display, "version": item.terminology_version},
                "terminology_status": item.terminology_status,
                "result_status": item.result_status,
                "observed_at": item.observed_at.isoformat(),
                "version": item.version,
                "is_corrected": item.is_corrected,
                "is_late_arrival": item.is_late_arrival,
            }
            for item in observations[:100]
        ],
        "conditions": [
            {
                "id": item.id,
                "source_id": item.source_id,
                "clinical_status": item.clinical_status,
                "snomed": {"code": item.snomed_code, "display": item.snomed_display, "version": item.snomed_version},
                "terminology_status": item.terminology_status,
                "recorded_at": item.recorded_at.isoformat(),
            }
            for item in conditions
        ],
        "diagnostic_reports": [
            {
                "id": item.id,
                "status": item.status,
                "coding": {"system": item.coding_system, "code": item.code, "display": item.display, "version": item.terminology_version},
                "effective_at": item.effective_at.isoformat() if item.effective_at else None,
            }
            for item in reports
        ],
    }


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def service_status() -> dict:
    """Read public-safe service state. Requires status:read and returns no records or patient counts."""
    with SessionLocal() as db:
        authorize(db, "status:read", "service_status")
        batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at.desc()))
    return {
        "service": "Sentinel Network",
        "status": "available",
        "mode": "read_only",
        "data_label": "Synthetic data — demonstration only",
        "scheduler_mode": getattr(get_settings(), "scheduler_mode", "local_apscheduler"),
        "chain": batch.receipt_status if batch else "unavailable",
        "clinical_boundary": "Clinician review requested; no autonomous clinical action.",
    }


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def patient_record(patient_id: str) -> dict:
    """Read one synthetic patient record. Clinicians read their hospital; CAIO reads any synthetic hospital. No bulk listing tool exists."""
    with SessionLocal() as db:
        patient = authorize_patient(db, "patient_record", patient_id)
        return patient_summary(patient, db)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def patient_reference_lookup(patient_reference: str) -> dict:
    """Resolve one known authorised synthetic patient reference to its UUID. This is not a listing or bulk export tool."""
    if not isinstance(patient_reference, str) or not patient_reference.strip() or len(patient_reference) > 40:
        raise ValueError("A synthetic patient reference of 1-40 characters is required")
    with SessionLocal() as db:
        organisation_id = authorize_clinical_listing(db, "patient_reference_lookup")
        query = select(Patient).where(Patient.reference == patient_reference.strip())
        if organisation_id:
            query = query.where(Patient.organization_id == organisation_id)
        patient = db.scalar(query)
        if not patient:
            raise ValueError("No authorised synthetic patient matches that reference")
        return {
            "data_label": "Synthetic data — demonstration only",
            "patient": {"id": patient.id, "reference": patient.reference, "care_setting": patient.care_setting, "age_group": patient.age_group},
            "notice": "One exact reference per request; this endpoint cannot list or export a patient cohort.",
        }


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def patient_fhir_r4_bundle(patient_id: str) -> dict:
    """Export one authorised synthetic patient's bounded HL7 FHIR R4 (4.0.1) Bundle. It is not full FHIR server conformance."""
    with SessionLocal() as db:
        patient = authorize_patient(db, "patient_fhir_r4_bundle", patient_id)
        observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at)).all())
        conditions = list(db.scalars(select(Condition).where(Condition.patient_id == patient.id).order_by(Condition.recorded_at)).all())
        reports = list(db.scalars(select(DiagnosticReport).where(DiagnosticReport.patient_id == patient.id).order_by(DiagnosticReport.effective_at)).all())
        return everything_bundle(patient, observations, conditions, reports)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def patient_review_evidence(patient_id: str) -> dict:
    """Read deterministic guideline evidence, findings, alerts and prior MDT review history for one authorised synthetic patient."""
    with SessionLocal() as db:
        patient = authorize_patient(db, "patient_review_evidence", patient_id)
        observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at)).all())
        findings = list(db.scalars(select(Finding).where(Finding.patient_id == patient.id).order_by(Finding.created_at.desc())).all())
        alerts = list(db.scalars(select(Alert).where(Alert.patient_id == patient.id).order_by(Alert.id.desc())).all())
        reviews = list(db.scalars(select(MdtReview).where(MdtReview.patient_id == patient.id).order_by(MdtReview.created_at.desc())).all())
        return {
            "patient_id": patient.id,
            "data_label": "Synthetic data — demonstration only",
            "guideline_assessment": assessment(patient, observations),
            "findings": [{"id": item.id, "rule_id": item.rule_id, "rule_version": item.rule_version, "category": item.category, "status": item.status, "evidence": item.evidence, "created_at": item.created_at.isoformat()} for item in findings],
            "alerts": [{"id": item.id, "status": item.status, "acknowledged_at": item.acknowledged_at.isoformat() if item.acknowledged_at else None, "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None} for item in alerts],
            "mdt_reviews": [{"id": item.id, "report": item.report, "selected_pathways": item.selected_pathways, "clinician_attested_at": item.clinician_attested_at.isoformat() if item.clinician_attested_at else None} for item in reviews],
            "notice": "Evidence and MDT considerations are for clinician review only; they are not diagnoses, treatment instructions, urgency classifications, or referral orders.",
        }


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def review_queue(limit: int = 10) -> dict:
    """Read up to 20 current evidence-linked review alerts within the authorised clinician/CAIO clinical boundary."""
    if not isinstance(limit, int) or limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    with SessionLocal() as db:
        organisation_id = authorize_clinical_listing(db, "review_queue")
        query = select(Alert).join(Patient, Patient.id == Alert.patient_id).order_by(Alert.id.desc()).limit(limit)
        if organisation_id:
            query = query.where(Patient.organization_id == organisation_id)
        alerts = list(db.scalars(query).all())
        rows = []
        for item in alerts:
            patient = db.get(Patient, item.patient_id)
            finding = db.get(Finding, item.finding_id)
            rows.append({
                "alert_id": item.id,
                "status": item.status,
                "patient_id": patient.id if patient else None,
                "patient_reference": patient.reference if patient else None,
                "finding": {"id": finding.id, "rule_id": finding.rule_id, "category": finding.category, "status": finding.status, "created_at": finding.created_at.isoformat()} if finding else None,
            })
        return {"data_label": "Synthetic data — demonstration only", "returned": len(rows), "maximum": 20, "alerts": rows, "notice": "Read-only review evidence; no acknowledgement, resolution, triage, referral or notification action is available through MCP."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def sglt2_care_gap_review(limit: int = 20) -> dict:
    """List up to 50 synthetic T2DM records that meet a guideline criterion (ASCVD or CKD-EPI eGFR < 60) and have no active, verified SGLT2-inhibitor entry, within the authorised clinician/CAIO boundary. Clinician review list only; never a prescription."""
    with SessionLocal() as db:
        organisation_id = authorize_clinical_listing(db, "sglt2_care_gap_review")
        return sglt2_care_gaps(db, organisation_id, limit)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def terminology_catalogue() -> dict:
    """Read the small verified diabetes demo terminology catalogue; it is not a terminology server and never invents mappings."""
    with SessionLocal() as db:
        authorize(db, "status:read", "terminology_catalogue")
    return {
        "data_label": "Synthetic data — demonstration only",
        "verified_on": CATALOGUE_VERIFIED_ON,
        "limitations": "Small verified demo subset only. Unknown or ambiguous codes require terminology review and cannot drive detection.",
        "loinc": [{"system": LOINC_SYSTEM, "version": LOINC_VERSION, "code": item.loinc_code, "display": item.display, "accepted_ucum": list(item.accepted_ucum), "canonical_ucum": item.canonical_ucum, "selection_basis": item.selection_basis, "method": item.method} for item in LAB_TERMS.values()],
        "snomed": [{"system": SNOMED_SYSTEM, "code": code, **entry} for code, entry in SNOMED_CONDITIONS.items()],
        "atc": [{"system": ATC_SYSTEM, "index_last_updated": ATC_INDEX_DATE, "code": code, **entry} for code, entry in ATC_MEDICATIONS.items()],
        "ucum_system": UCUM_SYSTEM,
    }


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def integrity_status() -> dict:
    """Read source-batch commitment status only; raw source data and chain-write actions remain unavailable."""
    with SessionLocal() as db:
        authorize(db, "status:read", "integrity_status")
        batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at.desc()))
        if not batch:
            return {"data_label": "Synthetic data — demonstration only", "status": "not_seeded"}
        return {"data_label": "Synthetic data — demonstration only", "batch_id": batch.id, "schema_version": batch.schema_version, "commitment": batch.commitment, "receipt_status": batch.receipt_status, "chain_id": batch.chain_id, "transaction_hash": batch.transaction_hash, "block_number": batch.block_number, "notice": "Commitment metadata is not proof of clinical truth and MCP cannot anchor, tamper, restore, or revoke grants."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def governance_summary() -> dict:
    """Read patient-free governance controls and aggregate MCP telemetry. Requires CAIO governance:read."""
    with SessionLocal() as db:
        authorize(db, "governance:read", "governance_summary")
        dashboard = build_governance_dashboard(db)
        return {
            "data_label": dashboard["data_label"],
            "framework": dashboard["framework"],
            "system_profile": dashboard["system_profile"],
            "lifecycle": dashboard["lifecycle"],
            "risk_controls": dashboard["risk_controls"],
            "mcp_activity": dashboard["live_evidence"]["mcp_activity"],
            "current_gaps": dashboard["current_gaps"],
        }


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def mcp_activity() -> dict:
    """Read aggregate external-agent activity only. Requires CAIO governance:read; no identities, tokens or patient records are returned."""
    with SessionLocal() as db:
        authorize(db, "governance:read", "mcp_activity")
        return {"data_label": "Synthetic data — demonstration only", **mcp_activity_summary(db)}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def regional_aggregate() -> dict:
    """Read the fixed, small-cell-protected synthetic aggregate. Requires CAIO research:aggregate and verified source integrity."""
    with SessionLocal() as db:
        authorize(db, "research:aggregate", "regional_aggregate")
        batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at.desc()))
        if not batch or batch.receipt_status != "confirmed":
            return {"availability": "unavailable", "reason": "Aggregate access remains fail-closed until a source batch has a verified chain receipt."}
        integrity = verify_batch(batch, list(db.scalars(select(Observation)).all()))
        if integrity["chain_status"] != "verified":
            return {"availability": "unavailable", "reason": "Aggregate access remains fail-closed because source integrity is not verified."}
        rows = []
        for organisation in db.scalars(select(Organization).order_by(Organization.district)).all():
            patients = list(db.scalars(select(Patient).where(Patient.organization_id == organisation.id)).all())
            eligible = 0
            numerator = 0
            for patient in patients:
                result = assessment(patient, list(db.scalars(select(Observation).where(Observation.patient_id == patient.id)).all()))
                if result["status"] not in {"data_quality_review", "out_of_scope"}:
                    eligible += 1
                    numerator += result["status"] in {"evidence_requires_clinician_review", "confirmation_gap"}
            if eligible < 20:
                rows.append({"district": organisation.district, "suppressed": True})
            else:
                rows.append({"district": organisation.district, "eligible_patients": eligible, "review_evidence_patients": numerator, "anomaly_rate_among_monitored_patients": round(numerator / eligible * 100, 1), "suppressed": False})
        return {"availability": "available", "data_label": "Synthetic data — demonstration only", "metric": "Anomaly rate among monitored patients", "cohort_definition": "Unique synthetic patients with sufficient valid observations for the stated demo rule.", "rows": rows, "integrity": integrity}


class KeyProtectedMCP:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            if get_settings().mcp_enabled:
                initialise_database()
            await self.inner(scope, receive, send)
            return
        if scope["type"] != "http":
            return

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [(b"cache-control", b"no-store")]
            await send(message)

        if not get_settings().mcp_enabled:
            await JSONResponse({"detail": "MCP is disabled"}, status_code=503)(scope, receive, private_send)
            return
        headers = {key.lower(): value for key, value in scope["headers"]}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, token = authorization.partition(" ")
        key_id = None
        with SessionLocal() as db:
            key = None
            try:
                if scheme.lower() != "bearer" or not token.startswith("snt_mcp_") or len(token) > 128:
                    raise HTTPException(401)
                key = db.scalar(select(AgentAccessKey).where(AgentAccessKey.token_hash == hashlib.sha256(token.encode()).hexdigest()))
                valid_key(db, key)
                key_id = key.id
            except HTTPException:
                db.add(AuditEvent(actor_id=None, event_type="mcp.authentication_denied", entity_type="mcp", entity_id="unknown", detail={}))
                db.commit()
        if not key_id:
            await JSONResponse({"detail": "A valid owner-approved agent key is required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, private_send)
            return
        context = _key_id.set(key_id)
        try:
            await self.inner(scope, receive, private_send)
        finally:
            _key_id.reset(context)


app = KeyProtectedMCP(mcp.streamable_http_app())
