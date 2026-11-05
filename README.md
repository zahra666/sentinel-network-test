# Sentinel Network — MIT Hackathon DM

Sentinel Network is a local-first, synthetic-data-only hackathon MVP for transparent EMR anomaly surveillance, clinician review, privacy-bounded research aggregates, and blockchain-backed integrity checks.

> **Synthetic data — demonstration only.** This is not a diagnostic, prescribing, triage, referral, or production EMR system. Clinical decisions remain with a qualified clinician.

## Architecture

External agents can use the optional, separate read-only MCP listener. Access requires an expiring administrator-approved API key; patient reads also require exact-patient approval and the account's current clinical/hospital permissions. MCP is disabled by default. See [MCP_ACCESS.md](MCP_ACCESS.md) for startup, approval, revocation and connection instructions.

- `frontend/` — React + Vite desktop dashboard.
- `backend/` — FastAPI API, SQLAlchemy persistence, APScheduler worker, deterministic rule/CPG review tree.
- `contracts/` — Hardhat Solidity integrity and research-grant contract.
- `supabase/` — cloud Postgres migration and RLS policy tests; the browser never queries clinical data directly.

The default no-credential local mode uses SQLite. Supabase Postgres is the cloud data authority when `DATABASE_URL` is configured. A missing chain configuration is displayed as **unavailable/pending**; it is never represented as a simulated success.

## Evidence, Sentinel Agent, and MDT boundaries

- The data seed contains exactly 100 fictional profiles: **45 Outpatient Clinic, 25 Medical Ward, 18 Surgical Ward, and 12 O&G Ward** across three fictional hospital/district pairs.
- The deterministic, versioned CPG review tree performs data validity, applicability, evidence/confirmation, monitoring audit, and clinician-owned management/referral consideration. It is evidence support only—not diagnosis, treatment selection, urgency classification, or a referral order.
- O&G records marked pregnancy-related are deliberately returned as **out of scope**, because this CPG does not cover diabetes in pregnancy.
- **Sentinel Agent** is an authorised, persisted chat/orchestration layer. It can queue role-scoped evidence review, integrity verification, fixed aggregate-analysis handoff, or a public-source crawl restricted to HTTPS MOH/MEMS pages. It never passes patient data to the crawler.
- A separately deployable, optional **MCP listener** is disabled by default. An administrator can issue a one-time, time-limited, hashed key to a named active user with either status-only or explicitly enumerated patient-read scope. The listener exposes two read-only tools, enforces hospital isolation, uses no browser-origin access by default, and records every allow/deny decision. Dashboard login tokens are never MCP credentials.
- Its background workflow creates deduplicated **in-app** reminders and clinician-review-queue escalations only. It never contacts patients or external services.
- The three-person Diabetes MDT is intentionally deterministic rather than an ANN: an evidence/confirmation reviewer, data-quality/complexity reviewer, and care-coordination reviewer. It can present endocrinology, bariatric-surgery, or GP-continuity **considerations** only. A clinician must select and attest an offered consideration; no referral is sent.

The CPG mapping is a demo interpretation and requires a named Malaysian clinical owner to review it before any non-demo use.

## Local quick start — Windows PowerShell

```powershell
Copy-Item .env.example .env
.\scripts\start.ps1
```

Then open `http://localhost:5173`. The API is available at `http://localhost:8000/docs`.

Demo accounts are created only in the local synthetic database:

| Role | Email | Password |
| --- | --- | --- |
| Administrator | admin@sentinel.demo | SentinelDemo!2026 |
| Aster clinician | clinician.aster@sentinel.demo | SentinelDemo!2026 |
| Approved researcher | researcher.approved@sentinel.demo | SentinelDemo!2026 |
| Denied researcher | researcher.denied@sentinel.demo | SentinelDemo!2026 |

## AI governance lifecycle MVP

Administrators can open **AI Governance** to review Sentinel's evidence-backed lifecycle controls. The page maps the local MVP to ISO/IEC 42001:2023 management-system themes and ISO/IEC 5338:2023 lifecycle practices. It is an alignment demonstrator only—not ISO certification or a full compliance claim.

The future LLM-assisted Sentinel Agent is in scope, but no provider or model is configured in this build. The dashboard records the permitted and prohibited LLM inputs, mandatory structured-output validation and deterministic fallback, clinician-review boundary, live local evidence, known gaps, and an administrator's 30-day governance-review attestation. LLM activation remains blocked until its provider/model identity, synthetic-only evaluation, telemetry, validation, and safety acceptance evidence are implemented.

## Local blockchain demo

```powershell
Set-Location contracts
npm install
npm run chain
```

In another terminal:

```powershell
Set-Location contracts
npm run deploy:local
```

Copy the emitted `CHAIN_CONTRACT_ADDRESS` and local RPC URL to `.env`, restart the API, then use **Anchor batch** in the Integrity Ledger. The contract stores only commitments and metadata—never raw synthetic records. For a hosted Sepolia demo, use a dedicated test wallet, set `CHAIN_RPC_URL`, `CHAIN_PRIVATE_KEY`, `CHAIN_CONTRACT_ADDRESS`, and the public `CHAIN_RESEARCHER_ADDRESS`; then anchor the synthetic research grant before allowing an aggregate query. Revocation fails closed if the on-chain revocation transaction cannot be confirmed.

## Supabase data authority

The committed schema migration is [20261104163106_initial_sentinel_schema.sql](supabase/migrations/20261104163106_initial_sentinel_schema.sql). Every exposed `public` table has RLS enabled and neither `anon` nor `authenticated` is granted table access. The browser uses the FastAPI API only; it never includes a Supabase client or service credential. Run [001_rls_denial.sql](supabase/tests/001_rls_denial.sql) against a disposable Supabase database after applying the migration.

Use a server-only Postgres connection string for `DATABASE_URL` on Render. Do not put that URL, a Supabase service key, or a private key in `VITE_*` variables or the static site.

## Healthcare interoperability — focused FHIR R4 MVP

The API adds a deliberately bounded **HL7 FHIR R4 (4.0.1) JSON** exchange layer over the existing SQL tables. It is not a full FHIR server, a profile-validation service, a terminology server, or a production interoperability claim. All exchanges remain synthetic-only and require an authenticated administrator or clinician; the existing clinician organisation boundary applies on every import and export. Researchers remain aggregate-only.

| Supported resource | Bounded MVP behaviour |
| --- | --- |
| `Patient` | Matches an existing synthetic profile by its Sentinel identifier or existing UUID. It never creates a new patient. |
| `Observation` | Imports/export quantitative `valueQuantity` observations with a timezone-bearing effective time, result status, LOINC coding, UCUM unit code, local source-code extension, source identifier, and `meta.versionId`. |
| `Condition` | Preserves incoming source coding (including ICD); verifies only the tiny SNOMED CT demo subset below. No automatic ICD-to-SNOMED mapping occurs. |
| `DiagnosticReport` | Imports/exports status, coded report, patient reference, timestamps, source/version, and patient-scoped `Observation` result references. |

Endpoints are intentionally patient-scoped: `GET /api/fhir/r4/Patient/{id}`, `GET /api/fhir/r4/Patient/{id}/$everything`, `GET /api/fhir/r4/Observation/{id}`, `GET /api/fhir/r4/Condition/{id}`, `GET /api/fhir/r4/DiagnosticReport/{id}`, and `POST /api/fhir/r4/import`. Import accepts only a `Bundle` with `type: "collection"`, 1–100 supported resources, and does not implement general FHIR transaction semantics. Corrections use the same source identifier with a higher `meta.versionId`; prior source versions are retained, linked, and trigger the normal deterministic scan/review workflow.

### Verified demo terminology catalogue

This is a small, reviewable catalogue—not a replacement for hospital terminology governance. Every record retains its original local code, value and unit alongside coding/system/version and any derived normalised value. Unknown or ambiguous terms are stored with a `needs_terminology_review` state and cannot feed a detection rule. Detection uses verified code mappings, final/amended/corrected status, and compatible canonical UCUM units; it never matches a display name.

| Purpose | LOINC / SNOMED CT | Canonical UCUM | Deliberate constraint |
| --- | --- | --- | --- |
| HbA1c, NGSP | `http://loinc.org` `4548-4` | `%` | Blood mass-fraction result. |
| HbA1c, IFCC | `http://loinc.org` `59261-8` | `%` derived from `mmol/mol` | The original IFCC value remains; only `NGSP = (0.09148 × IFCC) + 2.152` is supported and tested. |
| Glucose, molar | `14749-6` | `mmol/L` | A verified local source-code context (`FPG` or `RPG`) is required before a diabetes-rule key is assigned. |
| Glucose, mass | `2345-7` | `mmol/L` derived from `mg/dL` | Only the explicit `mg/dL ÷ 18.0156` conversion is supported. |
| Creatinine | `2160-0` | `mg/dL` | Stored for provenance; it is not used as a substitute for eGFR. |
| eGFR, CKD-EPI | `62238-1` | `mL/min/{1.73_m2}` | The CKD-EPI creatinine-based equation remains part of the code meaning; unspecified/different equations are not combined in a trend. |
| Type 2 diabetes condition | `http://snomed.info/sct` `44054006` | n/a | Single verified synthetic-demo condition concept only. Incoming terminology version is preserved when supplied. No SNOMED CT release is bundled or redistributed. |

The selection rationale is based on the LOINC component/property/system/scale/method and units, rather than names alone. Catalogue verification links: [HL7 FHIR R4 Observation](https://hl7.org/fhir/R4/observation.html), [DiagnosticReport](https://hl7.org/fhir/R4/diagnosticreport.html), [Condition](https://hl7.org/fhir/R4/condition.html), [LOINC 4548-4](https://loinc.org/4548-4), [LOINC 59261-8](https://loinc.org/59261-8), [LOINC 14749-6](https://loinc.org/14749-6), [LOINC 62238-1](https://loinc.org/62238-1), [UCUM specification](https://ucum.org/ucum), [NGSP IFCC conversion](https://ngsp.org/ifcc.asp), and [SNOMED CT documentation for 44054006](https://docs.snomed.org/snomed-ct-practical-guides/snomed-ct-clinical-decision-support-guide/1-introduction/1.1-overview).

The importer validates only this UCUM subset: `%`, `mmol/mol`, `mmol/L`, `mg/dL`, and `mL/min/{1.73_m2}`. Any other unit code, or a recognised LOINC/unit mismatch, is rejected rather than normalised speculatively.

Use the sample only with an existing synthetic profile; it is not suitable for real records:

```powershell
$login = Invoke-RestMethod http://localhost:8000/api/auth/login -Method POST -ContentType 'application/json' -Body '{"email":"admin@sentinel.demo","password":"SentinelDemo!2026"}'
$headers = @{ Authorization = "Bearer $($login.access_token)" }
$patient = (Invoke-RestMethod http://localhost:8000/api/patients -Headers $headers)[0]
Invoke-RestMethod "http://localhost:8000/api/fhir/r4/Patient/$($patient.id)/`$everything" -Headers $headers
$bundle = (Get-Content .\examples\fhir-r4-import.json -Raw).Replace('REPLACE_WITH_EXISTING_SYNTHETIC_PATIENT_UUID', $patient.id).Replace('REPLACE_WITH_EXISTING_SYNTHETIC_REFERENCE', $patient.reference)
Invoke-RestMethod http://localhost:8000/api/fhir/r4/import -Method POST -Headers $headers -ContentType 'application/json' -Body $bundle
```

The FHIR endpoints have automated coverage for round trips, duplicate idempotency, invalid resource/timestamp/unit rejection, patient-reference resolution, ambiguous terminology review states, source correction provenance/reprocessing, supported conversions, and clinical/research organisation controls. Remote Supabase migration/RLS verification remains pending until the user selects a new Supabase project.

## Cloud release contract

1. Run Supabase migrations and RLS tests; do not expose direct table access to browser clients.
2. Deploy the API and worker as separate Render services with server-only database and Sepolia credentials.
3. Deploy the contract to Sepolia with a dedicated test wallet.
4. Push the secret-scanned repository to **MIT Hackathon DM**.
5. Publish `frontend/dist` privately to Sites. If that fails, deploy the identical static frontend to a protected Vercel project.

Before the static build, set `VITE_API_BASE_URL` to the HTTPS Render API URL plus `/api`; unlike Vite development mode, production has no proxy.

See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the five-minute walkthrough.
