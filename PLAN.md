# Sentinel Network implementation checklist

- [x] Define synthetic-only, clinician-review-only boundaries.
- [x] Define the 100-profile care-setting mix and guideline decision-tree contract.
- [x] Build and test the FastAPI Sync -> Analyse -> Route vertical slice.
- [x] Build the React dashboard against live API responses.
- [x] Validate the local Hardhat contract and integrity flow.
- [x] Add Supabase migrations, RLS tests, GitHub CI, and deployment manifests.
- [ ] Verify a private static Sites publish, with protected Vercel fallback only if Sites cannot publish.
- [x] Expand the read-only MCP surface for clinician organisation-scoped and CAIO synthetic system-read profiles; add FHIR, evidence, review-queue, terminology, integrity, governance and aggregate tools, with hashed expiring/revocable keys and audits.
- [x] Add CAIO-only governance/dashboard access, aggregate MCP activity telemetry, and explicitly labelled five-minute simulation sessions.
- [ ] Obtain and confirm a $0 Supabase project quote, then run the cloud migration/seed/RLS verification and configure the Free Render combined API/MCP service plus GitHub scheduler.
- [ ] Configure HTTPS routing and verify an off-machine GrokBot MCP client using a short-lived key; standalone MCP extraction remains a later handoff.
- [x] Deliver a bounded HL7 FHIR R4 interoperability vertical slice over the existing SQL model: authenticated patient-scoped import/export for Patient, Observation, Condition, and DiagnosticReport; no claim of full FHIR conformance.
- [x] Add a verified diabetes-demo terminology catalogue (LOINC, UCUM, and a small SNOMED CT subset), original-code/value provenance, corrected-result reprocessing, code/unit-gated evidence rules, migrations, and access-control round-trip tests. Remote Supabase RLS execution remains pending.
- [x] Surface readable coded observations in the clinical UI and document the supported FHIR subset, conversion limits, terminology licensing limits, and sample requests.

The application must never ingest real patient data, autonomously diagnose, prescribe, classify urgency, or submit referrals.
