import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import mcp_server
from app.db import Base, get_db
from app.main import app
from app.models import AgentAccessKey, AuditEvent, Observation, Organization, Patient, User
from app.security import create_token


@pytest.fixture
def access(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "SessionLocal", factory)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: SimpleNamespace(mcp_enabled=True))
    monkeypatch.setattr(mcp_server, "initialise_database", lambda: None)
    with factory() as db:
        org = Organization(code="A", name="Synthetic A", district="Synthetic district")
        other = Organization(code="B", name="Synthetic B", district="Other district")
        db.add_all([org, other]); db.flush()
        admin = User(email="admin@test.demo", password_hash="unused", role="administrator")
        clinician = User(email="clinician@test.demo", password_hash="unused", role="clinician", organization_id=org.id)
        caio = User(email="caio@test.demo", password_hash="unused", role="caio")
        researcher = User(email="researcher@test.demo", password_hash="unused", role="researcher")
        db.add_all([admin, clinician, caio, researcher]); db.flush()
        patients = [Patient(reference=f"SYN-{i}", organization_id=o.id, care_setting="Outpatient Clinic", age_group="45-64")
                    for i, o in enumerate([org, org, other])]
        db.add_all(patients); db.flush()
        db.add(Observation(patient_id=patients[0].id, code="demo_lab", value=6.0, unit="%", observed_at=datetime.now(timezone.utc), source_id="demo-source"))
        db.commit()

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    client = TestClient(app)
    admin_headers = {"Authorization": f"Bearer {create_token(admin)}"}

    def issue(**overrides):
        payload = {"name": "Test agent", "purpose": "Synthetic test", "user_id": clinician.id, "scopes": ["status:read"], **overrides}
        return client.post("/api/mcp/keys", json=payload, headers=admin_headers)

    yield SimpleNamespace(factory=factory, client=client, issue=issue, admin=admin, caio=caio, clinician=clinician,
                          researcher=researcher, patients=patients, headers=admin_headers)
    app.dependency_overrides.pop(get_db, None)
    client.close()
    engine.dispose()


def fresh_listener():
    # SDK managers are single-use; each test listener needs a fresh lifecycle.
    mcp_server.mcp._session_manager = None
    return mcp_server.KeyProtectedMCP(mcp_server.mcp.streamable_http_app())


def rpc(token=None, method="tools/list", params=None, origin=None):
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if origin:
        headers["Origin"] = origin
    with TestClient(fresh_listener(), base_url="http://localhost:8001") as client:
        return client.post("/mcp", headers=headers,
                           json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def test_no_key_invalid_key_and_login_token_denied(access):
    for token in [None, "snt_mcp_invalid", create_token(access.admin)]:
        response = rpc(token)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert "SYN-" not in response.text


def test_status_key_cannot_read_any_patient_or_arbitrary_tool(access):
    issued = access.issue()
    assert issued.status_code == 200
    issued_payload = issued.json()
    token = issued_payload["api_key"]
    response = rpc(token, "tools/call", {"name": "service_status", "arguments": {}})
    assert response.status_code == 200
    assert not response.json()["result"].get("isError")
    for name, arguments in [("patient_record", {"patient_id": access.patients[0].id}), ("read_file", {"path": ".env"})]:
        response = rpc(token, "tools/call", {"name": name, "arguments": arguments})
        assert response.json()["result"]["isError"]
        assert "demo_lab" not in response.text


def test_organisation_scoped_clinician_access_and_hospital_isolation(access):
    issued = access.issue(scopes=["clinical:read:organisation"])
    token = issued.json()["api_key"]
    allowed = rpc(token, "tools/call", {"name": "patient_record", "arguments": {"patient_id": access.patients[0].id}})
    assert not allowed.json()["result"].get("isError")
    assert "demo_lab" in allowed.text
    same_organisation = rpc(token, "tools/call", {"name": "patient_record", "arguments": {"patient_id": access.patients[1].id}})
    assert not same_organisation.json()["result"].get("isError")
    denied = rpc(token, "tools/call", {"name": "patient_record", "arguments": {"patient_id": access.patients[2].id}})
    assert denied.json()["result"]["isError"]
    assert access.patients[2].reference not in denied.text
    assert access.issue(scopes=["clinical:read:system"]).status_code == 400
    # A changed hospital assignment takes effect at every read.
    with access.factory() as db:
        owner = db.get(User, access.clinician.id)
        owner.organization_id = access.patients[2].organization_id
        db.commit()
    assert rpc(token, "tools/call", {"name": "patient_record", "arguments": {"patient_id": access.patients[0].id}}).json()["result"]["isError"]


def test_useful_clinician_read_tools_are_bounded(access):
    token = access.issue(scopes=["status:read", "clinical:read:organisation"]).json()["api_key"]
    lookup = rpc(token, "tools/call", {"name": "patient_reference_lookup", "arguments": {"patient_reference": access.patients[0].reference}})
    assert not lookup.json()["result"].get("isError")
    assert access.patients[0].reference in lookup.text
    assert access.patients[2].reference not in lookup.text
    assert "cannot list" in lookup.text
    review = rpc(token, "tools/call", {"name": "patient_review_evidence", "arguments": {"patient_id": access.patients[0].id}})
    assert not review.json()["result"].get("isError")
    fhir = rpc(token, "tools/call", {"name": "patient_fhir_r4_bundle", "arguments": {"patient_id": access.patients[0].id}})
    assert not fhir.json()["result"].get("isError")
    catalogue = rpc(token, "tools/call", {"name": "terminology_catalogue", "arguments": {}})
    assert not catalogue.json()["result"].get("isError")
    assert "4548-4" in catalogue.text
    assert rpc(token, "tools/call", {"name": "patient_reference_lookup", "arguments": {"patient_reference": access.patients[2].reference}}).json()["result"]["isError"]


def test_caio_has_system_synthetic_read_but_no_write_or_key_control(access):
    issued = access.issue(
        user_id=access.caio.id,
        scopes=["status:read", "clinical:read:system", "governance:read", "research:aggregate"],
    )
    issued_payload = issued.json()
    token = issued_payload["api_key"]
    cross_hospital = rpc(token, "tools/call", {"name": "patient_record", "arguments": {"patient_id": access.patients[2].id}})
    assert not cross_hospital.json()["result"].get("isError")
    governance = rpc(token, "tools/call", {"name": "governance_summary", "arguments": {}})
    assert not governance.json()["result"].get("isError")
    assert "token_hash" not in governance.text and issued_payload["api_key"] not in governance.text
    activity = rpc(token, "tools/call", {"name": "mcp_activity", "arguments": {}})
    assert not activity.json()["result"].get("isError")
    denied = rpc(token, "tools/call", {"name": "approve_key", "arguments": {}})
    assert denied.json()["result"]["isError"]
    caio_headers = {"Authorization": f"Bearer {create_token(access.caio)}"}
    assert access.client.get("/api/governance", headers=caio_headers).status_code == 200
    assert access.client.get("/api/patients", headers=caio_headers).status_code == 403
    assert access.client.post("/api/mcp/keys", json={"name": "no", "purpose": "no", "user_id": access.caio.id, "scopes": ["status:read"]}, headers=caio_headers).status_code == 403


@pytest.mark.parametrize("change", ["expired", "revoked", "inactive", "approver_demoted", "role_changed"])
def test_access_changes_take_effect_immediately(access, change):
    issued = access.issue(scopes=["clinical:read:organisation"]).json()
    with access.factory() as db:
        key = db.get(AgentAccessKey, issued["id"])
        if change == "expired":
            key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif change == "inactive":
            db.get(User, access.clinician.id).active = False
        elif change == "approver_demoted":
            db.get(User, access.admin.id).role = "researcher"
        elif change == "role_changed":
            db.get(User, access.clinician.id).role = "researcher"
        db.commit()
    if change == "revoked":
        assert access.client.post(f"/api/mcp/keys/{issued['id']}/revoke", headers=access.headers).status_code == 200
    result = rpc(issued["api_key"], "tools/call", {"name": "patient_record", "arguments": {"patient_id": access.patients[0].id}})
    assert result.status_code == 401 or result.json()["result"]["isError"]
    assert "demo_lab" not in result.text


def test_approval_restrictions_and_key_secrecy(access):
    assert access.issue(scopes=["clinical:read:system"]).status_code == 400
    assert access.issue(user_id=access.researcher.id, scopes=["clinical:read:organisation"]).status_code == 400
    assert access.issue(expires_in_minutes=1441).status_code == 422
    token = access.issue().json()["api_key"]
    listing = access.client.get("/api/mcp/keys", headers=access.headers)
    assert token not in listing.text and "token_hash" not in listing.text
    with access.factory() as db:
        key = db.scalar(select(AgentAccessKey))
        assert key.token_hash != token and len(key.token_hash) == 64
        assert token not in str([event.detail for event in db.scalars(select(AuditEvent)).all()])
    for user in [access.clinician, access.researcher]:
        headers = {"Authorization": f"Bearer {create_token(user)}"}
        assert access.client.post("/api/mcp/keys", json={"name": "self", "purpose": "self", "user_id": user.id}, headers=headers).status_code == 403
        assert access.client.get("/api/mcp/keys", headers=headers).status_code == 403
    assert access.client.get("/api/mcp/keys", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_disabled_origin_host_and_audit(access, monkeypatch):
    token = access.issue().json()["api_key"]
    assert rpc(token, origin="https://untrusted.example").status_code == 403
    with TestClient(fresh_listener(), base_url="http://untrusted.example") as client:
        assert client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={}).status_code == 421
    rpc(token, "tools/call", {"name": "service_status", "arguments": {}})
    rpc(token, "tools/call", {"name": "patient_record", "arguments": {"patient_id": access.patients[0].id}})
    with access.factory() as db:
        events = {event.event_type for event in db.scalars(select(AuditEvent)).all()}
        assert {"mcp.key_approved", "mcp.tool_allowed", "mcp.tool_denied"} <= events
    monkeypatch.setattr(mcp_server, "get_settings", lambda: SimpleNamespace(mcp_enabled=False))
    assert rpc(token).status_code == 503


def test_official_mcp_client_handshake_and_reads(access):
    token = access.issue(scopes=["status:read", "clinical:read:organisation"]).json()["api_key"]

    async def run():
        listener = fresh_listener()
        async with mcp_server.mcp.session_manager.run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=listener),
                                         headers={"Authorization": f"Bearer {token}"}) as http:
                async with streamable_http_client("http://localhost:8001/mcp", http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        assert initialized.serverInfo.name == "Sentinel Network"
                        tools = await session.list_tools()
                        assert {tool.name for tool in tools.tools} == {"service_status", "patient_record", "patient_reference_lookup", "patient_fhir_r4_bundle", "patient_review_evidence", "review_queue", "terminology_catalogue", "integrity_status", "governance_summary", "mcp_activity", "regional_aggregate"}
                        status = await session.call_tool("service_status", {})
                        assert not status.isError
                        patient = await session.call_tool("patient_record", {"patient_id": access.patients[0].id})
                        assert not patient.isError
                        denied = await session.call_tool("patient_record", {"patient_id": access.patients[2].id})
                        assert denied.isError
    asyncio.run(run())
