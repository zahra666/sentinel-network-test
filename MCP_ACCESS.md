# Sentinel external-agent access

**Synthetic data — demonstration only.** This remains a synthetic-only clinical demonstration. These controls have local automated coverage; they do not authorize real patient data or establish production privacy compliance.

The MCP listener is separate from the dashboard API. It exposes two read-only tools using the official Python MCP SDK's Streamable HTTP transport:

| Tool | Required permission | Returned data |
| --- | --- | --- |
| `service_status` | `status:read` | Static capabilities and service availability; no patient records, patient counts, or operational history |
| `patient_record(patient_id)` | `patient:read`, explicit approval of this exact patient, and a current clinical role | Explicit patient fields and at most 100 recent observations; marked synthetic with a clinician-review notice |

Every connection requires an independently generated MCP Bearer key. An ordinary key defaults to status access. Clinical permission is an administrator's explicit approval of exact patients, a purpose, an active account and an expiry. No wildcard approval or patient listing exists. The account's current hospital restriction applies again at every clinical read. Researchers cannot receive clinical scopes.

There is no filesystem, shell, arbitrary-query, conversation-history, user-directory, credential, clinical write, or permission-management tool. Existing dashboard login tokens are rejected by the MCP listener, and MCP keys are rejected by the dashboard API.

## Windows setup

From the project root, install/update the backend if needed:

```powershell
uv sync --project backend --extra dev --locked
```

Alternatively, with the existing startup-created environment:

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
```

Start/restart the ordinary dashboard backend so its new administrator endpoints are loaded. Keep that API private. Startup adds the new `agent_access_keys` table through the existing SQLAlchemy initializer; no existing data is reset. Start the MCP listener separately:

```powershell
.\scripts\start-mcp.ps1
```

This explicitly enables MCP for that process, binds only to `127.0.0.1`, and serves `http://127.0.0.1:8001/mcp`. Keep the process running while connecting agents; Ctrl+C stops it. Normal application startup does not start MCP. No access keys are seeded. Starting `uvicorn app.mcp_server:app` directly requires `MCP_ENABLED=true`; otherwise HTTP requests return 503.

macOS/Linux equivalent, from `backend/`:

```sh
MCP_ENABLED=true .venv/bin/python -m uvicorn app.mcp_server:app --host 127.0.0.1 --port 8001
```

Both processes must use the same configured database. The MCP process does not need the scheduler worker to serve these tools. Cloud-database migration/RLS and public hosting have not been verified in this change.

## Approve a connection

Use the existing local API documentation at `http://localhost:8000/docs`:

1. Log in through `POST /api/auth/login` with the administrator account, then use **Authorize** with its returned login token.
2. Obtain the intended account ID from that account's login response (`user.id`) or authenticated `/api/auth/me` response, then authorize the administrator again to issue the approval. For clinical access, select a clinician account and copy exact patient UUIDs from the administrator's `GET /api/patients` response. Do not use real patient information.
3. Call `POST /api/mcp/keys` with the approval below. The returned `api_key` is shown only in that response. Store it in the external agent's secret settings.

Status-only approval:

```json
{
  "name": "My external agent",
  "purpose": "Check Sentinel service availability",
  "user_id": "REPLACE_WITH_ACTIVE_ACCOUNT_ID",
  "scopes": ["status:read"],
  "patient_ids": [],
  "expires_in_minutes": 60
}
```

For a clinical demonstration, select a clinician account, add `patient:read` to `scopes`, and provide exact approved patient UUIDs in `patient_ids`. Approval is limited to 20 patients and 24 hours per key; the default expiry is one hour. Permissions cannot be expanded by an agent. To change them, revoke the old key and issue a new approval.

`GET /api/mcp/keys` lists approval metadata for administrators without returning keys or hashes. `POST /api/mcp/keys/{key_id}/revoke` immediately blocks subsequent requests. Expiry, account deactivation, or disabling/demoting the approving administrator also blocks access. Current role and hospital changes are enforced at each patient read.

## Configure an external MCP client

Use a client that supports **Streamable HTTP with a custom Bearer header**:

```json
{
  "url": "http://127.0.0.1:8001/mcp",
  "headers": {
    "Authorization": "Bearer REPLACE_WITH_OWNER_APPROVED_MCP_KEY"
  }
}
```

This is a connection-field example; adapt it to the client's configuration format. OAuth-only clients that cannot supply a Bearer header are not supported by this version. Never put a key in a URL, browser frontend code, prompts, source control, or query parameters.

For agents on another computer, provide a configured HTTPS reverse proxy to **only** the MCP listener. Set `MCP_ALLOWED_HOSTS` to the exact public hostname accepted by the proxy (preserve the original Host header). Leave `MCP_ALLOWED_ORIGINS` empty for headless clients, or explicitly allow trusted browser origins. The startup helper's environment variable can be used to set the public host:

```powershell
$env:MCP_ALLOWED_HOSTS = 'your-mcp-host.example,127.0.0.1:8001,localhost:8001'
.\scripts\start-mcp.ps1
```

Keep the dashboard, its demo login/accounts, and database private. Public HTTPS routing, rate limiting, key delivery and a live off-machine connection are deployment work and have not been configured or tested here. No public URL or key has been created by this implementation. Do not expose the dashboard's bundled demo credentials to the internet.

## Storage and verification

Keys contain 256 bits of generated randomness; only their SHA-256 digest is stored. Approval/revocation, authentication denial, and allowed/denied named tool calls are recorded in `audit_events` without raw keys, observations, or tool arguments. MCP responses and key-issuance/list responses use `Cache-Control: no-store`. SDK host/origin protection remains enabled; MCP bodies are limited to 16 KiB. These controls do not prevent an authorized recipient from retaining information already returned.

Run verification from `backend/`:

```powershell
.venv/Scripts/python.exe -m pytest -q
```

Coverage includes the official MCP client's initialize/list/call flow, absent/invalid keys, rejection of dashboard login tokens, status-only denials, exact-patient and hospital isolation, researcher denial, expiry, revocation, role/account/approver changes, secret-safe listing/storage, host/origin restrictions, audit events and disabled mode. All tests use isolated synthetic fixtures. No external notifications or patient actions are sent.
