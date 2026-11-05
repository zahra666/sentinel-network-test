# Sentinel Network implementation checklist

- [x] Define synthetic-only, clinician-review-only boundaries.
- [x] Define the 100-profile care-setting mix and guideline decision-tree contract.
- [x] Build and test the FastAPI Sync -> Analyse -> Route vertical slice.
- [x] Build the React dashboard against live API responses.
- [x] Validate the local Hardhat contract and integrity flow.
- [x] Add Supabase migrations, RLS tests, GitHub CI, and deployment manifests.
- [ ] Verify a private static publish, with Vercel fallback only if needed.
- [x] Implement the separate read-only MCP listener, hashed expiring/revocable keys, exact-patient approval, hospital isolation, access audits, and official MCP client tests (18 backend tests passed).
- [ ] Configure HTTPS routing and verify an off-machine MCP client; cloud migration/RLS remains unverified for the new key table.
- [x] Deliver a bounded HL7 FHIR R4 interoperability vertical slice over the existing SQL model: authenticated patient-scoped import/export for Patient, Observation, Condition, and DiagnosticReport; no claim of full FHIR conformance.
- [x] Add a verified diabetes-demo terminology catalogue (LOINC, UCUM, and a small SNOMED CT subset), original-code/value provenance, corrected-result reprocessing, code/unit-gated evidence rules, migrations, and access-control round-trip tests. Remote Supabase RLS execution remains pending.
- [x] Surface readable coded observations in the clinical UI and document the supported FHIR subset, conversion limits, terminology licensing limits, and sample requests.

The application must never ingest real patient data, autonomously diagnose, prescribe, classify urgency, or submit referrals.
