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


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    care_setting: Mapped[str] = mapped_column(String(40), index=True)
    age_group: Mapped[str] = mapped_column(String(32))
    symptomatic: Mapped[bool] = mapped_column(Boolean, default=False)
    pregnancy_related: Mapped[bool] = mapped_column(Boolean, default=False)
    body_mass_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    documented_complexity: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    observations: Mapped[list["Observation"]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    conditions: Mapped[list["Condition"]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    diagnostic_reports: Mapped[list["DiagnosticReport"]] = relationship(back_populates="patient", cascade="all, delete-orphan")


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
