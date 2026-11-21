# Sentinel external-agent MCP access

**Synthetic data — demonstration only.** This is a bounded, read-only MCP surface for the Sentinel hackathon MVP. It is not a production clinical integration, a general FHIR server, a terminology server, or authority to act on patients.

## Access profiles

An administrator creates a named, purpose-bound, hashed, expiring and revocable MCP key for an active account. Dashboard login tokens are never MCP keys.

| Owner profile | Permitted scopes | Clinical boundary |
| --- | --- | --- |
| Clinician | `status:read`, `clinical:read:organisation` | Supported synthetic evidence within the clinician's current hospital only. |
| CAIO | `status:read`, `clinical:read:system`, `governance:read`, `research:aggregate` | Supported records across all synthetic hospitals, plus patient-free governance and fixed aggregate views. This system-wide scope is hackathon-demo-only. |

Keys are valid for 1–1,440 minutes. Account deactivation, role/profile change, administrator demotion, key revocation, or expiry blocks the next call immediately. There is no wildcard administrator MCP profile.

## Read-only tools

| Tool | Clinician | CAIO | Boundary |
| --- | --- | --- | --- |
| `service_status`, `integrity_status`, `terminology_catalogue` | Yes | Yes | Operational/terminology metadata only. |
| `patient_reference_lookup` | Own hospital | All synthetic hospitals | Resolves one exact known synthetic reference; it cannot list or export a cohort. |
| `patient_record`, `patient_fhir_r4_bundle`, `patient_review_evidence` | Own hospital | All synthetic hospitals | One supplied patient UUID at a time. FHIR is the documented R4 subset only. |
| `review_queue` | Own hospital | All synthetic hospitals | Maximum 20 evidence-linked alerts; read only. |
| `sglt2_care_gap_review` | Own hospital | All synthetic hospitals | Maximum 50 records meeting ASCVD / eGFR < 60 with no active, verified SGLT2-inhibitor entry; clinician review list only, never a prescription. |
| `governance_summary`, `mcp_activity`, `regional_aggregate` | No | Yes | Governance is patient-free; regional aggregate remains small-cell-protected and fails closed until source integrity is verified. |

There are no tools for bulk database export, filesystem/shell, arbitrary SQL, credentials, key management, FHIR import, scan execution, alert acknowledgement, referral attestation, patient contact, data injection, chain writes, grant changes, tamper/restore, diagnosis, prescribing, triage or referral orders.

`mcp_activity` reports aggregate telemetry only. “Observed agents” means distinct approved keys with successful stateless calls in the preceding five minutes; it is not a persistent connection count. Governance demo sessions are separate, time-limited simulation records and never invoke a clinical MCP tool.

## Local setup

```powershell
uv sync --project backend --extra dev --locked
.\scripts\start.ps1
.\scripts\start-mcp.ps1
```

The local MCP listener binds to `http://127.0.0.1:8001/mcp`. It shares the configured database with the dashboard API, but it is a separate process locally. `MCP_ENABLED=true` is required for the MCP process.

For the deployed zero-cost demo, FastAPI mounts the same protected listener at `https://YOUR-RENDER-SERVICE.onrender.com/mcp`; set `MCP_ENABLED=true`, `MCP_ALLOWED_HOSTS` to the Render hostname, and retain a restricted `MCP_ALLOWED_ORIGINS` list. Headless clients can use a Bearer header without a browser origin.

## Approve and revoke a key

1. Sign in as `admin@sentinel.demo` through `POST /api/auth/login` and use the returned dashboard token with `POST /api/mcp/keys`.
2. Obtain the intended clinician or CAIO account UUID from that account's login response or `/api/auth/me` response.
3. Request only the profile's allowed scopes. The plaintext `api_key` appears once; put it directly into the external agent's secret configuration.

Clinician example:

```json
{
  "name": "Clinician evidence reviewer",
  "purpose": "Synthetic organisation-scoped evidence review",
  "user_id": "REPLACE_WITH_CLINICIAN_UUID",
  "scopes": ["status:read", "clinical:read:organisation"],
  "expires_in_minutes": 60
}
```

CAIO example:

```json
{
  "name": "CAIO governance reviewer",
  "purpose": "Synthetic system-read hackathon demonstration",
  "user_id": "REPLACE_WITH_CAIO_UUID",
  "scopes": ["status:read", "clinical:read:system", "governance:read", "research:aggregate"],
  "expires_in_minutes": 30
}
```

`GET /api/mcp/keys` returns metadata only to administrators. `POST /api/mcp/keys/{key_id}/revoke` blocks the next call. Do not put a key in a URL, browser source, prompt transcript, repository, or query parameter.

## Client configuration and GrokBot trial

Use a Streamable HTTP client with a custom Bearer header:

```json
{
  "url": "https://YOUR-RENDER-SERVICE.onrender.com/mcp",
  "headers": {
    "Authorization": "Bearer REPLACE_WITH_SHORT_LIVED_MCP_KEY"
  }
}
```

For the first GrokBot test, issue a CAIO key with **only** `status:read`, call `service_status`, verify the governance dashboard increments observed activity, then revoke the key and verify the next call is denied. Add clinical scopes only when you explicitly choose the synthetic test record and purpose.

## Verification and later extraction

Run `uv run pytest -q` in `backend/` and `npm run build` in `frontend/`. The test suite covers SDK handshake/list/call behavior, clinician hospital isolation, CAIO system-read behavior, denied control tools, expiration/revocation, secret-safe key storage, aggregate-only telemetry, time-bounded simulation sessions and disabled mode.

The MCP server remains in this repository while its clinical authorisation model is being validated. A later `sentinel-network-mcp` repository may extract only the connector and its typed configuration/tests; it must not contain seed data, database URLs, credentials or issued keys.
