"""Evidence-backed AI governance metadata for the Sentinel MVP.

This module intentionally describes only controls that exist in the local
demonstration. It is not an ISO certification assessment or legal compliance
determination.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AgentAccessKey, Alert, AuditEvent, GovernanceReview, McpDemoSession, Scan, SourceBatch, User

GOVERNANCE_VERSION = "sentinel-governance-v1"
REVIEW_INTERVAL_DAYS = 30


LLM_PROFILE = {
    "component_id": "sentinel-agent-llm",
    "component_name": "Sentinel Agent LLM assistance",
    "integration_status": "planned",
    "is_configured": False,
    "provider": "not configured",
    "model": "not configured",
    "model_version": "not configured",
    "intended_purpose": [
        "Rewrite minimum-necessary, authorised synthetic evidence into an approved review schema.",
        "Assist with role-scoped task orchestration and public-source reference handling.",
    ],
    "permitted_inputs": [
        "Minimum-necessary synthetic evidence for an authorised user and workflow.",
        "Approved public reference material from allowlisted sources.",
        "Task metadata that contains no patient identity or full record history.",
    ],
    "prohibited_inputs": [
        "A complete patient database, bulk observation export, credentials, or chain private keys.",
        "Real patient data, direct identifiers, or data outside the user's role and organisation boundary.",
        "Instructions to diagnose, prescribe, triage, submit referrals, or contact patients.",
    ],
    "required_controls": [
        "Deterministic clinical rules remain the authoritative detection mechanism.",
        "Structured output validation is required before any LLM-generated summary is displayed.",
        "A deterministic template is retained when validation fails, evidence is contradicted, or the model is unavailable.",
        "Clinician review is required; no LLM output can trigger a clinical action automatically.",
        "Agent tools remain role-scoped, auditable, and blocked from prohibited data and actions.",
    ],
    "activation_requirements": [
        "Select and record provider, model, model version, prompt version, and retention terms.",
        "Complete synthetic-only evaluation, structured-output validation, and safety acceptance testing.",
        "Instrument request counts, blocked inputs, validation failures, latency, and model/provider errors.",
    ],
}


RISK_CONTROLS = [
    {
        "id": "clinical-autonomy",
        "risk": "Unsupported autonomous clinical decision or patient contact.",
        "status": "implemented",
        "controls": [
            "Clinician review is requested for every finding.",
            "The system does not diagnose, prescribe, triage, refer, or contact patients.",
        ],
    },
    {
        "id": "data-quality",
        "risk": "Invalid, missing, corrected, or late-arriving evidence produces an unreliable result.",
        "status": "implemented",
        "controls": [
            "Units are checked before comparison and incompatible values are not compared.",
            "Observation versions and source provenance are retained for audit.",
        ],
    },
    {
        "id": "access-boundary",
        "risk": "Unauthorised clinical or research access.",
        "status": "implemented",
        "controls": [
            "Role and organisation checks are enforced by the backend.",
            "Research queries are fixed aggregate queries and fail closed without grant/integrity evidence.",
        ],
    },
    {
        "id": "integrity",
        "risk": "Undetected source-data alteration or an overstated blockchain claim.",
        "status": "partial",
        "controls": [
            "Source batches use versioned manifests and commitments.",
            "Chain state is shown as pending or unavailable until a real receipt exists.",
        ],
    },
    {
        "id": "llm-agent",
        "risk": "Future LLM output exposes data, invents unsupported claims, or bypasses human review.",
        "status": "planned",
        "controls": [
            "The LLM profile defines input limits, validation, fallback, and human-review gates before activation.",
            "Provider, model evaluation, telemetry, and model-change controls are not yet implemented.",
        ],
    },
]


def _serialize_scan(scan: Scan | None) -> dict | None:
    if not scan:
        return None
    return {
        "status": scan.status,
        "trigger": scan.trigger,
        "started_at": scan.started_at.isoformat(),
        "finished_at": scan.finished_at.isoformat() if scan.finished_at else None,
        "scanned_count": scan.scanned_count,
        "finding_count": scan.finding_count,
        "skipped_count": scan.skipped_count,
    }


def _serialize_review(review: GovernanceReview | None, db: Session) -> dict | None:
    if not review:
        return None
    actor = db.get(User, review.actor_id)
    reviewed_at = review.reviewed_at if review.reviewed_at.tzinfo else review.reviewed_at.replace(tzinfo=timezone.utc)
    next_due = reviewed_at + timedelta(days=REVIEW_INTERVAL_DAYS)
    return {
        "id": review.id,
        "reviewed_at": reviewed_at.isoformat(),
        "reviewed_by": actor.email if actor else "unknown governance reviewer",
        "note": review.note,
        "next_review_due": next_due.isoformat(),
        "status": "overdue" if datetime.now(timezone.utc) > next_due else "not_due",
        "evidence_snapshot": review.evidence_snapshot,
    }


def mcp_activity_summary(db: Session) -> dict:
    """Return only aggregate, non-identifying MCP telemetry for governance."""
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(minutes=5)
    daily_cutoff = now - timedelta(hours=24)
    recent_agent_count = db.scalar(
        select(func.count(func.distinct(AuditEvent.entity_id))).where(
            AuditEvent.event_type == "mcp.tool_allowed", AuditEvent.created_at >= recent_cutoff
        )
    ) or 0
    active_key_count = db.scalar(
        select(func.count()).select_from(AgentAccessKey).where(
            AgentAccessKey.revoked_at.is_(None), AgentAccessKey.expires_at > now
        )
    ) or 0
    revoked_key_count = db.scalar(
        select(func.count()).select_from(AgentAccessKey).where(AgentAccessKey.revoked_at.is_not(None))
    ) or 0
    expired_key_count = db.scalar(
        select(func.count()).select_from(AgentAccessKey).where(
            AgentAccessKey.revoked_at.is_(None), AgentAccessKey.expires_at <= now
        )
    ) or 0
    active_simulation_count = db.scalar(
        select(func.count()).select_from(McpDemoSession).where(
            McpDemoSession.ended_at.is_(None), McpDemoSession.expires_at > now
        )
    ) or 0
    allowed_calls = db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.event_type == "mcp.tool_allowed", AuditEvent.created_at >= daily_cutoff
        )
    ) or 0
    denied_calls = db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.event_type.in_(("mcp.tool_denied", "mcp.authentication_denied")),
            AuditEvent.created_at >= daily_cutoff,
        )
    ) or 0
    return {
        "observed_agents_last_five_minutes": recent_agent_count,
        "active_simulated_sessions": active_simulation_count,
        "active_keys": active_key_count,
        "revoked_keys": revoked_key_count,
        "expired_keys": expired_key_count,
        "allowed_calls_last_24_hours": allowed_calls,
        "denied_calls_last_24_hours": denied_calls,
        "definition": "Observed agents are distinct approved keys with successful stateless MCP calls in the previous five minutes; they are not persistent network connections.",
        "simulation_definition": "Simulated sessions are time-bounded governance-demo records and never invoke clinical MCP tools.",
    }


def build_governance_dashboard(db: Session) -> dict:
    """Build a patient-free, evidence-backed governance view for administrators."""
    settings = get_settings()
    latest_scan = db.scalar(select(Scan).order_by(Scan.started_at.desc()))
    batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at.desc()))
    latest_review = db.scalar(select(GovernanceReview).order_by(GovernanceReview.reviewed_at.desc()))
    alert_counts = dict(db.execute(select(Alert.status, func.count()).group_by(Alert.status)).all())
    audit_count = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    latest_audit = list(db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(5)).all())

    source_batch_state = "available" if batch else "not_seeded"
    data_status = "implemented" if batch else "partial"
    lifecycle = [
        {
            "id": "scope-accountability",
            "name": "Scope & accountability",
            "phase": "Plan",
            "status": "partial",
            "summary": "Synthetic-only scope and administrator accountability are defined; formal ownership and policy approval are not yet implemented.",
            "evidence": [
                {"label": "System scope", "value": "Synthetic clinical decision support and research integrity MVP"},
                {"label": "Accountable MVP role", "value": "Administrator"},
            ],
        },
        {
            "id": "risk-impact",
            "name": "Risk & impact controls",
            "phase": "Plan",
            "status": "partial",
            "summary": "Safety boundaries and a focused risk-control register exist; a formal impact assessment and treatment workflow remain outstanding.",
            "evidence": [
                {"label": "Clinical autonomy", "value": "Blocked by system boundary"},
                {"label": "LLM impact assessment", "value": "Required before activation"},
            ],
        },
        {
            "id": "data-governance",
            "name": "Data governance",
            "phase": "Do",
            "status": data_status,
            "summary": "Synthetic observations retain source, version, timing, and unit evidence; incompatible units are rejected from comparison.",
            "evidence": [
                {"label": "Data classification", "value": "Synthetic data — demonstration only"},
                {"label": "Latest source batch", "value": source_batch_state},
            ],
        },
        {
            "id": "llm-validation",
            "name": "LLM/agent design & validation",
            "phase": "Do",
            "status": "partial",
            "summary": "Current agent tools are role-scoped and auditable. The planned LLM has required guardrails but no configured provider, evaluation, or runtime telemetry.",
            "evidence": [
                {"label": "LLM integration", "value": LLM_PROFILE["integration_status"] + " / not configured"},
                {"label": "Authoritative clinical detection", "value": "Deterministic CPG review tree"},
            ],
        },
        {
            "id": "human-oversight",
            "name": "Operations & human oversight",
            "phase": "Do",
            "status": "implemented",
            "summary": "Role-scoped review queues, clinician acknowledgement, and non-autonomous operation are enforced in the MVP.",
            "evidence": [
                {"label": "Open review alerts", "value": str(alert_counts.get("new", 0))},
                {"label": "Latest scan", "value": latest_scan.status if latest_scan else "not run"},
            ],
        },
        {
            "id": "monitoring-traceability",
            "name": "Monitoring & traceability",
            "phase": "Check",
            "status": "partial",
            "summary": "Audit events, system state, and integrity commitments are visible; the chain is not treated as confirmed without a real receipt.",
            "evidence": [
                {"label": "Audit events", "value": str(audit_count)},
                {"label": "Chain status", "value": batch.receipt_status if batch else "unavailable"},
            ],
        },
        {
            "id": "continual-improvement",
            "name": "Continual improvement",
            "phase": "Act",
            "status": "partial",
            "summary": "Thirty-day governance review attestations are recorded; formal corrective-action and incident-remediation workflows are not yet implemented.",
            "evidence": [
                {"label": "Latest governance review", "value": latest_review.reviewed_at.isoformat() if latest_review else "review required"},
                {"label": "Review interval", "value": f"{REVIEW_INTERVAL_DAYS} days"},
            ],
        },
    ]

    return {
        "data_label": "Synthetic data — demonstration only",
        "governance_version": GOVERNANCE_VERSION,
        "framework": {
            "primary": "ISO/IEC 42001:2023",
            "lifecycle_companion": "ISO/IEC 5338:2023",
            "claim": "MVP alignment demonstrator — not ISO certified or a full compliance claim.",
        },
        "system_profile": {
            "system_name": "Sentinel Network",
            "system_type": "Synthetic clinical decision support and research integrity MVP",
            "ai_scope": "A future LLM-assisted agent layer is in scope. No LLM provider or model is currently enabled.",
            "clinical_boundary": "No autonomous diagnosis, prescribing, triage, referral, or patient contact.",
        },
        "llm_profile": LLM_PROFILE,
        "lifecycle": lifecycle,
        "risk_controls": RISK_CONTROLS,
        "live_evidence": {
            "latest_scan": _serialize_scan(latest_scan),
            "alert_counts": {"new": alert_counts.get("new", 0), "acknowledged": alert_counts.get("acknowledged", 0), "resolved": alert_counts.get("resolved", 0)},
            "source_batch": {"status": source_batch_state, "receipt_status": batch.receipt_status if batch else "unavailable", "created_at": batch.created_at.isoformat() if batch else None},
            "scheduler": {"enabled": settings.schedule_enabled, "timezone": settings.schedule_timezone},
            "llm_runtime": {"configured": False, "status": "planned / not configured"},
            "mcp_activity": mcp_activity_summary(db),
            "recent_audit_events": [{"event_type": event.event_type, "entity_type": event.entity_type, "created_at": event.created_at.isoformat()} for event in latest_audit],
        },
        "current_gaps": [
            "No LLM provider, model version, model evaluation, or runtime telemetry is configured.",
            "Formal named ownership, policy approval, competency records, and independent clinical validation are outside this MVP.",
            "Corrective-action and incident-remediation workflows have not been implemented.",
        ],
        "latest_review": _serialize_review(latest_review, db),
        "review_interval_days": REVIEW_INTERVAL_DAYS,
    }


def review_snapshot(dashboard: dict) -> dict:
    """Keep a small immutable record of what was reviewed without patient data."""
    return {
        "governance_version": dashboard["governance_version"],
        "framework": dashboard["framework"],
        "lifecycle_statuses": [{"id": item["id"], "status": item["status"]} for item in dashboard["lifecycle"]],
        "llm_runtime": dashboard["live_evidence"]["llm_runtime"],
        "source_batch": dashboard["live_evidence"]["source_batch"],
        "scheduler": dashboard["live_evidence"]["scheduler"],
    }
