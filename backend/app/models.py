from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return str(uuid.uuid4())


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    district: Mapped[str] = mapped_column(String(80))


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentAccessKey(Base):
    __tablename__ = "agent_access_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(240))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    patient_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpDemoSession(Base):
    """A transparent, time-bounded dashboard simulation; never an MCP credential."""

    __tablename__ = "mcp_demo_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    label: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    care_setting: Mapped[str] = mapped_column(String(40), index=True)
    age_group: Mapped[str] = mapped_column(String(32))
    # Administrative sex for the synthetic cohort ("female" / "male"); used for
    # sex-specific reference intervals and the CKD-EPI creatinine equation.
    sex: Mapped[str | None] = mapped_column(String(16), nullable=True)
    symptomatic: Mapped[bool] = mapped_column(Boolean, default=False)
    pregnancy_related: Mapped[bool] = mapped_column(Boolean, default=False)
    body_mass_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    documented_complexity: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    observations: Mapped[list["Observation"]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    conditions: Mapped[list["Condition"]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    diagnostic_reports: Mapped[list["DiagnosticReport"]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    medications: Mapped[list["MedicationStatement"]] = relationship(back_populates="patient", cascade="all, delete-orphan")


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("source_id", "version", name="uq_source_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(48), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    is_late_arrival: Mapped[bool] = mapped_column(Boolean, default=False)
    # Original source representation remains authoritative; normalised values
    # are an explicitly limited derived layer for compatible-rule evaluation.
    original_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    coding_system: Mapped[str | None] = mapped_column(String(160), nullable=True)
    standard_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    coding_display: Mapped[str | None] = mapped_column(String(240), nullable=True)
    terminology_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminology_status: Mapped[str] = mapped_column(String(48), default="needs_terminology_review", index=True)
    clinical_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ucum_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalised_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalised_ucum_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_status: Mapped[str] = mapped_column(String(32), default="final")
    method: Mapped[str | None] = mapped_column(String(160), nullable=True)
    egfr_equation: Mapped[str | None] = mapped_column(String(160), nullable=True)
    fhir_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    corrects_observation_id: Mapped[str | None] = mapped_column(ForeignKey("observations.id"), nullable=True, index=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    patient: Mapped[Patient] = relationship(back_populates="observations")


class Condition(Base):
    __tablename__ = "conditions"
    __table_args__ = (UniqueConstraint("source_id", "version", name="uq_condition_source_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    source_id: Mapped[str] = mapped_column(String(96), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    clinical_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_coding_system: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    source_display: Mapped[str | None] = mapped_column(String(240), nullable=True)
    source_terminology_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snomed_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    snomed_display: Mapped[str | None] = mapped_column(String(240), nullable=True)
    snomed_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminology_status: Mapped[str] = mapped_column(String(48), default="needs_terminology_review", index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fhir_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    patient: Mapped[Patient] = relationship(back_populates="conditions")


class DiagnosticReport(Base):
    __tablename__ = "diagnostic_reports"
    __table_args__ = (UniqueConstraint("source_id", "version", name="uq_report_source_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    source_id: Mapped[str] = mapped_column(String(96), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32))
    coding_system: Mapped[str | None] = mapped_column(String(160), nullable=True)
    code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    display: Mapped[str | None] = mapped_column(String(240), nullable=True)
    terminology_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_observation_ids: Mapped[list] = mapped_column(JSON, default=list)
    fhir_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    patient: Mapped[Patient] = relationship(back_populates="diagnostic_reports")


class MedicationStatement(Base):
    """Synthetic current-medication list entry (read-only evidence, never an order).

    Stored internally for the SGLT2 care-gap review; not exposed as a FHIR
    resource in this MVP. The original source coding is retained separately from
    the verified ATC mapping, and unverified entries cannot drive a rule.
    """
    __tablename__ = "medication_statements"
    __table_args__ = (UniqueConstraint("source_id", "version", name="uq_medication_source_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    source_id: Mapped[str] = mapped_column(String(96), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    source_coding_system: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    source_display: Mapped[str | None] = mapped_column(String(240), nullable=True)
    atc_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    atc_display: Mapped[str | None] = mapped_column(String(160), nullable=True)
    terminology_status: Mapped[str] = mapped_column(String(48), default="needs_terminology_review", index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    patient: Mapped[Patient] = relationship(back_populates="medications")


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    trigger: Mapped[str] = mapped_column(String(32))
    scanned_count: Mapped[int] = mapped_column(Integer, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("patient_id", "rule_id", "evidence_hash", name="uq_finding_evidence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scan_id: Mapped[str] = mapped_column(ForeignKey("scans.id"), index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(80), index=True)
    rule_version: Mapped[str] = mapped_column(String(24))
    category: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(40))
    evidence_hash: Mapped[str] = mapped_column(String(64), index=True)
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"), unique=True, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="new")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceBatch(Base):
    __tablename__ = "source_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    schema_version: Mapped[str] = mapped_column(String(32), default="sentinel-source-v1")
    nonce: Mapped[str] = mapped_column(String(128))
    manifest: Mapped[list] = mapped_column(JSON)
    commitment: Mapped[str] = mapped_column(String(64), index=True)
    on_chain_commitment: Mapped[str | None] = mapped_column(String(66), nullable=True)
    chain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contract_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    block_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchGrant(Base):
    __tablename__ = "research_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    purpose: Mapped[str] = mapped_column(String(160))
    scope: Mapped[str] = mapped_column(String(160))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chain_grant_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GovernanceReview(Base):
    __tablename__ = "governance_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class NotificationAttempt(Base):
    __tablename__ = "notification_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"), index=True)
    recipient_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(32), default="queued")
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    tool_events: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DelegatedTask(Base):
    __tablename__ = "delegated_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(48), index=True)
    input_data: Mapped[dict] = mapped_column(JSON, default=dict)
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MdtReview(Base):
    __tablename__ = "mdt_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    report: Mapped[dict] = mapped_column(JSON)
    clinician_attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clinician_attested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    selected_pathways: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SimulationRun(Base):
    """An administrator-started, accelerated-time synthetic data run for the live monitor."""

    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    started_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tick_seconds: Mapped[int] = mapped_column(Integer, default=5)
    simulated_days_per_tick: Mapped[int] = mapped_column(Integer, default=7)
    simulated_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    simulated_now: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ticks: Mapped[int] = mapped_column(Integer, default=0)
    observations_written: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # patient_id -> review state after the latest step, used to report state transitions.
    last_statuses: Mapped[dict] = mapped_column(JSON, default=dict)


class LiveStatusSnapshot(Base):
    """Aggregate review-state counts captured after each live-monitor scan; no patient identifiers."""

    __tablename__ = "live_status_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("simulation_runs.id"), nullable=True, index=True)
    scan_id: Mapped[str | None] = mapped_column(ForeignKey("scans.id"), nullable=True, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    simulated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # [{"organization_id", "care_setting", "status", "patients"}] plus per-organisation open alerts.
    counts: Mapped[list] = mapped_column(JSON, default=list)
    open_alerts: Mapped[dict] = mapped_column(JSON, default=dict)
    # What this step actually did: written observations, scan stage counts, state transitions.
    events: Mapped[dict] = mapped_column(JSON, default=dict)


class PatientContactConsent(Base):
    """Recorded patient preference for reminder emails. Synthetic addresses use the reserved .invalid domain."""

    __tablename__ = "patient_contact_consents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), unique=True, index=True)
    contact_email: Mapped[str] = mapped_column(String(180))
    email_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    share_results: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PatientMessage(Base):
    """An agent-drafted patient reminder. Nothing leaves the system until staff approve it; delivery is a simulated outbox."""

    __tablename__ = "patient_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    channel: Mapped[str] = mapped_column(String(24), default="email")
    template: Mapped[str] = mapped_column(String(48))
    recipient: Mapped[str] = mapped_column(String(180))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    includes_results: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="awaiting_approval", index=True)
    drafted_by_agent: Mapped[str] = mapped_column(String(48), default="sentinel_agent")
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery: Mapped[str] = mapped_column(String(32), default="simulated_outbox")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CareEvent(Base):
    """Synthetic annual-review history for the complication radar (eye and foot exams,
    urine ACR, LDL cholesterol, blood pressure). Kept out of the source batch and FHIR export."""
    __tablename__ = "care_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_secondary: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[str | None] = mapped_column(String(48), nullable=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
