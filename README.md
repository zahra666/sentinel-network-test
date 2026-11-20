# Sentinel Network — MIT Hackathon DM

Sentinel Network is a local-first, synthetic-data-only hackathon MVP for transparent EMR anomaly surveillance, clinician review, privacy-bounded research aggregates, and blockchain-backed integrity checks.

> **Synthetic data — demonstration only.** This is not a diagnostic, prescribing, triage, referral, or production EMR system. Clinical decisions remain with a qualified clinician.

## Architecture

External agents can use the optional read-only MCP listener. A clinician key can read supported synthetic evidence in its current hospital; a CAIO key can read the supported synthetic system-wide demo surface, governance and fixed aggregate views. Keys are administrator-approved, hashed, expiring, auditable and revocable. MCP is disabled by default. See [MCP_ACCESS.md](MCP_ACCESS.md) for the bounded tools, profiles, approval, revocation and GrokBot trial.

- `frontend/` — React + Vite desktop dashboard.
- `backend/` — FastAPI API, SQLAlchemy persistence, APScheduler worker, deterministic rule/CPG review tree.
- `contracts/` — Hardhat Solidity integrity and research-grant contract.
- `supabase/` — cloud Postgres migration and RLS policy tests; the browser never queries clinical data directly.

The default no-credential local mode uses SQLite. Supabase Postgres is the cloud data authority when `DATABASE_URL` is configured. A missing chain configuration is displayed as **unavailable/pending**; it is never represented as a simulated success.

## Evidence, Sentinel Agent, and MDT boundaries

- The data seed contains exactly 100 fictional profiles: **45 Outpatient Clinic, 25 Medical Ward, 18 Surgical Ward, and 12 O&G Ward** across three fictional hospital/district pairs.
- The cohort size is configurable for a fresh database with `SEED_PATIENT_COUNT` (12–10,000; code default 100, `.env.example` uses 500). The care-setting mix scales proportionally and the documented fixtures repeat every 100 profiles; tests use the default 100. To re-seed, delete `backend/sentinel.db` (or use a new `DATABASE_URL`). For about 5,000 profiles, set `LIVE_SIMULATION_TICK_SECONDS=10` because each simulated step re-scans every profile (about 3 seconds locally).
- The deterministic, versioned CPG review tree performs data validity, applicability, evidence/confirmation, monitoring audit, and clinician-owned management/referral consideration. It is evidence support only—not diagnosis, treatment selection, urgency classification, or a referral order.
- O&G records marked pregnancy-related are deliberately returned as **out of scope**, because this CPG does not cover diabetes in pregnancy.
- **Sentinel Agent** is an authorised, persisted chat/orchestration layer. It can queue role-scoped evidence review, integrity verification, fixed aggregate-analysis handoff, or a public-source crawl restricted to HTTPS MOH/MEMS pages. It never passes patient data to the crawler.
- The optional **MCP listener** is disabled by default. It offers bounded, read-only tools for authorised synthetic evidence, FHIR R4 export, deterministic guideline/MDT review history, review queues, terminology, integrity, governance and fixed aggregates. Clinician keys remain hospital-scoped; the CAIO system-read profile is hackathon-demo-only. It never exposes a bulk export, writes, referrals, credentials or infrastructure control. Dashboard login tokens are never MCP credentials.
- Its background workflow creates deduplicated **in-app** reminders and clinician-review-queue escalations only. It never contacts patients or external services.
- The three-person Diabetes MDT is intentionally deterministic rather than an ANN: an evidence/confirmation reviewer, data-quality/complexity reviewer, and care-coordination reviewer. It can present endocrinology, bariatric-surgery, or GP-continuity **considerations** only. A clinician must select and attest an offered consideration; no referral is sent.

The CPG mapping is a demo interpretation and requires a named Malaysian clinical owner to review it before any non-demo use.

## Local quick start — Windows PowerShell

```powershell
Copy-Item .env.example .env
.\scripts\start.ps1
```

Then open `http://localhost:5173`. The API is available at `http://localhost:8000/docs`.

These synthetic-only demo accounts are seeded in local development and the
explicitly provisioned Supabase demonstration dataset. They must not be reused
outside this hackathon MVP:

| Role | Email | Password |
| --- | --- | --- |
| Administrator | admin@sentinel.demo | 12345 |
| CAIO governance reviewer | caio@sentinel.demo | 12345 |
| Aster clinician | clinician.aster@sentinel.demo | 12345 |
| Approved researcher | researcher.approved@sentinel.demo | 12345 |
| Denied researcher | researcher.denied@sentinel.demo | 12345 |

## Live Monitor (staff-only)

**Live Monitor** is a single staff dashboard (administrators and clinicians; clinicians see only their own hospital). It shows the share of monitored synthetic T2DM profiles in each deterministic CPG review state over time, the mix by care setting, open review alerts, the newest alerts, and one selected patient's verified HbA1c/FPG trend. It refreshes every 3 seconds while a simulation runs and every 15 seconds otherwise.

- Only an administrator can **Start**, **Stop**, **Advance one step** or **Reset** the simulation. Researchers and the CAIO account cannot open it.
- Each step advances **accelerated simulated time** (default 7 simulated days every 5 real seconds; each profile is retested about every 30 simulated days). Every fictional profile has its own stable glycaemic baseline around the illustrative thresholds, a rising, falling or stable long-run trajectory, a personal cycle and measurement noise, plus a shared seasonal swing, so the review-state mix moves in both directions. The step writes fictional HbA1c and FPG results through the verified terminology catalogue, then runs the normal Sync → Analyse → Route scan. Every displayed state therefore comes from the existing review tree; nothing is drawn from hard-coded numbers.
- After each scan, an aggregate snapshot (counts by hospital, care setting and review state; no patient identifiers) is stored in `live_status_snapshots`.
- Simulated observations are outside the anchored source batch, so Integrity Ledger verification reports a mismatch and the research aggregate fails closed while they exist. **Reset simulation** removes simulated observations and the scans, findings and alerts they produced (audit events are kept), restoring the original verification.
- **Demo mode** (Live Monitor → Demo mode, or `/?demo`) plays a scripted *catch-up* storyline in 14-day steps: routine follow-up → missed follow-ups (few new results arrive) → recall clinic (a backlog of results lands, a subset returns higher values, and one lab batch sends FPG in mg/dL under the molar LOINC code, which the terminology gate rejects into data-quality review) → recovery (the lab re-sends corrected version-2 results, which supersede version 1 and are re-assessed) → routine, and stops itself after 24 steps. The left pane streams the actual INSERT statements, the scan call and worked rule checks for each step.
- A run stops automatically after `LIVE_SIMULATION_MAX_MINUTES` (default 30). Set `LIVE_SIMULATION_ENABLED=false` to disable the background runner. Cloud schema: [live monitor migration](supabase/migrations/20261120130000_add_live_monitor.sql).

## AI governance lifecycle MVP

Administrators and the dedicated **CAIO** demo account can open **AI Governance** to review Sentinel's evidence-backed lifecycle controls. CAIO browser access is restricted to governance and system status; it has no browser clinical or administrative controls. The page maps the local MVP to ISO/IEC 42001:2023 management-system themes and ISO/IEC 5338:2023 lifecycle practices. It is an alignment demonstrator only—not ISO certification or a full compliance claim.

The future LLM-assisted Sentinel Agent is in scope, but no provider or model is configured in this build. The dashboard records the permitted and prohibited LLM inputs, mandatory structured-output validation and deterministic fallback, clinician-review boundary, live local evidence, known gaps, and a 30-day governance-review attestation. It also reports aggregate external-agent activity: five-minute successful-key activity and separate five-minute simulated demo sessions. Neither metric is a persistent connection count and neither exposes key, user or patient data. LLM activation remains blocked until its provider/model identity, synthetic-only evaluation, telemetry, validation, and safety acceptance evidence are implemented.

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

The committed migrations establish the schema, time-bounded MCP-demo sessions and foreign-key index coverage: [initial schema](supabase/migrations/20261104163106_initial_sentinel_schema.sql), [MCP sessions](supabase/migrations/20261105170000_add_mcp_demo_sessions.sql), and [index coverage](supabase/migrations/20261105174500_add_foreign_key_indexes.sql). The idempotent [synthetic bootstrap](supabase/seed_synthetic.sql) creates only the documented fictional demo cohort. Every exposed `public` table has RLS enabled and neither `anon` nor `authenticated` is granted table access. The browser uses the FastAPI API only; it never includes a Supabase client or service credential. Run [001_rls_denial.sql](supabase/tests/001_rls_denial.sql) against a disposable Supabase database after applying the migrations.

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
$login = Invoke-RestMethod http://localhost:8000/api/auth/login -Method POST -ContentType 'application/json' -Body '{"email":"admin@sentinel.demo","password":"12345"}'
$headers = @{ Authorization = "Bearer $($login.access_token)" }
$patient = (Invoke-RestMethod http://localhost:8000/api/patients -Headers $headers)[0]
Invoke-RestMethod "http://localhost:8000/api/fhir/r4/Patient/$($patient.id)/`$everything" -Headers $headers
$bundle = (Get-Content .\examples\fhir-r4-import.json -Raw).Replace('REPLACE_WITH_EXISTING_SYNTHETIC_PATIENT_UUID', $patient.id).Replace('REPLACE_WITH_EXISTING_SYNTHETIC_REFERENCE', $patient.reference)
Invoke-RestMethod http://localhost:8000/api/fhir/r4/import -Method POST -Headers $headers -ContentType 'application/json' -Body $bundle
```

The FHIR endpoints have automated coverage for round trips, duplicate idempotency, invalid resource/timestamp/unit rejection, patient-reference resolution, ambiguous terminology review states, source correction provenance/reprocessing, supported conversions, and clinical/research organisation controls. Re-run the migration, seed and RLS-denial checks for every independently provisioned Supabase project before use.

## Cloud release contract

1. Request a new Aura Intelligence / Singapore Supabase project cost quote. Create the project only after it is confirmed as **$0** and the user confirms the quote. Apply every migration, seed the exact 100 synthetic profiles, and run the RLS-denial test. The browser never receives a Supabase URL, publishable key, service key or database URL.
2. Push the secret-scanned repository to **MIT Hackathon DM**. Configure one Free Render web service from `render.yaml`, with the FastAPI API and protected `/mcp` listener in the same process. Store `DATABASE_URL`, `APP_SECRET`, `SCHEDULER_TRIGGER_TOKEN`, MCP host/origin values and optional chain values in Render only.
3. The Free Render design deliberately does not deploy a background worker. Set `SCHEDULE_ENABLED=true` and `SCHEDULER_MODE=github_actions`; then enable the repository variable `SENTINEL_SCHEDULER_ENABLED=true` and configure `SENTINEL_API_URL` plus `SENTINEL_SCHEDULER_TOKEN` as GitHub Actions secrets. The included workflow calls the authenticated scan trigger daily at **02:00 Asia/Kuala_Lumpur**. Manual Scan Now remains available. Free web-service sleep and scheduler limitations remain visible in System Status.
4. Before the static build, set `VITE_API_BASE_URL` to the HTTPS Render API URL plus `/api`; unlike Vite development mode, production has no proxy. Build the frontend and publish `frontend/dist` privately to Sites. If Sites fails, deploy the identical static frontend to a protected Vercel project.
5. Smoke-test private Sites login, CAIO governance, MCP `/mcp`, scheduler authentication, Supabase RLS, and synthetic data counts. Test GrokBot first with a short-lived `status:read` CAIO key, observe its activity, then revoke it and verify denial.

Render Free services can sleep and do not supply a free background-worker plan, so this is a demonstrable hackathon deployment rather than a production scheduler. See [Render Free limits](https://render.com/docs/free).

See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the five-minute walkthrough.
