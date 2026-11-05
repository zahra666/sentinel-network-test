"""Separate read-only MCP listener; never mounts the dashboard or its login routes."""
from __future__ import annotations

import hashlib
from contextvars import ContextVar

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from starlette.responses import JSONResponse

from .config import get_settings
from .db import SessionLocal, initialise_database
from .mcp_access import valid_key
from .models import AgentAccessKey, AuditEvent, Observation, Patient

_key_id: ContextVar[str | None] = ContextVar("mcp_key_id", default=None)
settings = get_settings()
mcp = FastMCP(
    "Sentinel Network",
    instructions="Synthetic data — demonstration only. Read-only access requires owner-approved scope. Clinical evidence requires clinician review.",
    stateless_http=True, json_response=True, max_request_body_size=16384,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[value.strip() for value in settings.mcp_allowed_hosts.split(",") if value.strip()],
        allowed_origins=[value.strip() for value in settings.mcp_allowed_origins.split(",") if value.strip()],
    ),
)


def audit(db, key, tool: str, allowed: bool) -> None:
    db.add(AuditEvent(actor_id=key.user_id if key else None,
                      event_type="mcp.tool_allowed" if allowed else "mcp.tool_denied",
                      entity_type="agent_access_key", entity_id=key.id if key else "unknown",
                      detail={"tool": tool}))
    db.commit()


def authorize(db, scope: str, tool: str, patient_id: str | None = None):
    key = db.get(AgentAccessKey, _key_id.get()) if _key_id.get() else None
    try:
        owner = valid_key(db, key)
        if scope not in key.scopes:
            raise HTTPException(403)
        if patient_id is not None:
            if owner.role not in {"administrator", "clinician"} or patient_id not in key.patient_ids:
                raise HTTPException(403)
            patient = db.get(Patient, patient_id)
            if not patient or (owner.role == "clinician" and owner.organization_id != patient.organization_id):
                raise HTTPException(403)
    except HTTPException:
        audit(db, key, tool, False)
        raise ValueError("Access denied. Owner approval with the required scope is needed.") from None
    audit(db, key, tool, True)
    return owner


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def service_status() -> dict:
    """Read service capabilities only. Requires status:read; returns no records or patient counts."""
    with SessionLocal() as db:
        authorize(db, "status:read", "service_status")
    return {"service": "Sentinel Network", "status": "available", "mode": "read_only",
            "data_label": "Synthetic data — demonstration only", "patient_access": "explicit owner approval required"}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
def patient_record(patient_id: str) -> dict:
    """Read one explicitly approved synthetic patient and up to 100 recent observations. Requires patient:read and exact patient approval; hospital isolation applies."""
    if not isinstance(patient_id, str) or len(patient_id) > 36:
        raise ValueError("Invalid patient reference")
    with SessionLocal() as db:
        authorize(db, "patient:read", "patient_record", patient_id)
        patient = db.get(Patient, patient_id)
        observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id)
                                      .order_by(Observation.observed_at.desc(), Observation.id).limit(101)).all())
        # Explicit fields only: no users, notes, conversations, credentials, or arbitrary SQL.
        return {"id": patient.id, "reference": patient.reference, "care_setting": patient.care_setting,
                "age_group": patient.age_group, "data_label": "Synthetic data — demonstration only",
                "review_notice": "Clinician review requested; no diagnosis or treatment instruction.",
                "observations_truncated": len(observations) > 100,
                "observations": [{"id": item.id, "code": item.code, "value": item.value, "unit": item.unit,
                                  "observed_at": item.observed_at.isoformat(), "version": item.version}
                                 for item in observations[:100]]}


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
            await JSONResponse({"detail": "A valid owner-approved agent key is required"}, status_code=401,
                               headers={"WWW-Authenticate": "Bearer"})(scope, receive, private_send)
            return
        context = _key_id.set(key_id)
        try:
            await self.inner(scope, receive, private_send)
        finally:
            _key_id.reset(context)


app = KeyProtectedMCP(mcp.streamable_http_app())
