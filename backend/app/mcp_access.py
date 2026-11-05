"""Owner-controlled MCP credentials. Dashboard login tokens are never MCP keys."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import AgentAccessKey, AuditEvent, Patient, User
from .security import require_roles

router = APIRouter(prefix="/api/mcp/keys", tags=["MCP access — administrator approval"])
admin_only = require_roles("administrator")


class KeyApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=240)
    user_id: str
    scopes: list[Literal["status:read", "patient:read"]] = Field(default_factory=lambda: ["status:read"], min_length=1, max_length=2)
    patient_ids: list[str] = Field(default_factory=list, max_length=20)
    expires_in_minutes: int = Field(default=60, ge=1, le=1440)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def key_metadata(key: AgentAccessKey) -> dict:
    return {"id": key.id, "name": key.name, "purpose": key.purpose, "user_id": key.user_id,
            "approved_by": key.approved_by, "scopes": key.scopes, "patient_ids": key.patient_ids,
            "expires_at": utc(key.expires_at).isoformat(), "revoked": key.revoked_at is not None,
            "data_label": "Synthetic data — demonstration only"}


def valid_key(db: Session, key: AgentAccessKey | None) -> User:
    if not key or key.revoked_at or utc(key.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(401, "Invalid or expired agent key")
    owner = db.get(User, key.user_id)
    if not owner or not owner.active:
        raise HTTPException(401, "Invalid or expired agent key")
    # Approval loses effect if the approving administrator is disabled or demoted.
    approver = db.get(User, key.approved_by)
    if not approver or not approver.active or approver.role != "administrator":
        raise HTTPException(401, "Invalid or expired agent key")
    return owner


@router.post("")
def approve_key(payload: KeyApproval, response: Response, admin: User = Depends(admin_only), db: Session = Depends(get_db)) -> dict:
    owner = db.get(User, payload.user_id)
    if not owner or not owner.active:
        raise HTTPException(400, "An active account is required")
    if "patient:read" in payload.scopes:
        if owner.role not in {"administrator", "clinician"} or not payload.patient_ids:
            raise HTTPException(400, "Clinical role and explicit patient approval are required")
        for patient_id in payload.patient_ids:
            patient = db.get(Patient, patient_id)
            if not patient or (owner.role == "clinician" and owner.organization_id != patient.organization_id):
                raise HTTPException(400, "Patient approval exceeds the account's clinical access")
    elif payload.patient_ids:
        raise HTTPException(400, "Patient approval requires patient:read scope")
    secret = "snt_mcp_" + secrets.token_urlsafe(32)
    key = AgentAccessKey(token_hash=hashlib.sha256(secret.encode()).hexdigest(), name=payload.name,
                         purpose=payload.purpose, user_id=owner.id, approved_by=admin.id,
                         scopes=sorted(set(payload.scopes)), patient_ids=sorted(set(payload.patient_ids)),
                         expires_at=datetime.now(timezone.utc) + timedelta(minutes=payload.expires_in_minutes))
    db.add(key)
    db.flush()
    db.add(AuditEvent(actor_id=admin.id, event_type="mcp.key_approved", entity_type="agent_access_key", entity_id=key.id,
                      detail={"scopes": key.scopes, "approved_patient_count": len(key.patient_ids)}))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {**key_metadata(key), "api_key": secret, "notice": "Copy now; this key cannot be retrieved again."}


@router.get("")
def list_keys(response: Response, _admin: User = Depends(admin_only), db: Session = Depends(get_db)) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    return [key_metadata(key) for key in db.scalars(select(AgentAccessKey).order_by(AgentAccessKey.created_at.desc())).all()]


@router.post("/{key_id}/revoke")
def revoke_key(key_id: str, admin: User = Depends(admin_only), db: Session = Depends(get_db)) -> dict:
    key = db.get(AgentAccessKey, key_id)
    if not key:
        raise HTTPException(404, "Agent key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.add(AuditEvent(actor_id=admin.id, event_type="mcp.key_revoked", entity_type="agent_access_key", entity_id=key.id, detail={}))
        db.commit()
    return {"id": key.id, "revoked": True}
